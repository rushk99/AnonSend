from collections import Counter
from datetime import datetime

from django.contrib import messages
from django.core.files.uploadedfile import UploadedFile
from django.http import FileResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404

from .forms import UploadFileForm, PasswordForm
from .helper import get_analytics, compress_to_zip, get_hash
from .models import UploadFiles, Analytics


# Create your views here.
def index(request):
    """
    Displays the front page
    - Includes UploadFileForm
    - Takes input for UploadedFiles model
    - Input includes:
        - File
        - Password (optional, default:blank)
        - Expiry (default:10 Minutes)
        - Max downloads (default: 1)
    :param request: Django request
    :return: index.html
    """
    return render(request, 'index.html', {"form": UploadFileForm()})


def uploaded_link(request):
    """
    - Validates the form from index.html
    - Resolves file collisions by hashing
    - Links are generated with timestamp as seed

    :param request:
    :return: public_link and analytic_link
    """
    if request.method == "POST":
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            if len(request.FILES.getlist('file')) > 1:
                # Multiple files, compress to zip
                file = UploadedFile(**compress_to_zip(request.FILES.getlist('file')))
            else:
                # Single file
                file = request.FILES['file']
            # Set defaults for models and also store data retrieved from forms
            model = UploadFiles(file=file, file_name=file.name, file_hash=get_hash(file),
                                password=form.cleaned_data['password'],
                                expires_at=form.cleaned_data['expires_at'],
                                max_downloads=form.cleaned_data['max_downloads'])
            # Check for same file with hash
            query_set = UploadFiles.objects.all().filter(file_hash=model.file_hash)
            if query_set.exists():
                """ 
                Match found, no need to save
                Only update path
                """
                model.file = query_set.first().file
            else:
                """
                File does not exist,
                store in directory with same hash
                
                *Note*: Separate directory is created to mitigate errors in files with same name different contents
                """
                name = file.name
                name = name.split("/")
                name.insert(0, model.file_hash)
                name = "/".join(name)
                model.file.save(name, file)
            model.save()
            return render(request, 'upload_success.html',
                          {"public_link": model.public_link, "analytic_link": model.analytic_link})
        else:
            # Form is invalid
            return redirect(index)

    else:
        # Request is not post
        return redirect(index)


def public_link_handle(request, public_link):
    """
    - Serves as download page, intended to be shared via other platforms
    - If password is blank, set to visibility to false i.e hide password field
    - If password is invalid, prompt via messages
    - Finally verify constraints (max_downloads and expiry_at)
    - If within constraints, store info to Analytics model and serve the file

    :type public_link: str
    :param request:
    :param public_link: Uniquely generated public link
    :return: public_link.html
    """
    upload_file = get_object_or_404(UploadFiles, pk=public_link)
    visibility = upload_file.password != ""
    if request.method == "POST":
        form = PasswordForm(request.POST)

        if form.is_valid():
            if form.cleaned_data['password'] != upload_file.password:
                messages.error(request, "Invalid password")
                return render(request, 'public_link.html', {"visible": visibility, "form": PasswordForm()})
            download_count = len(Analytics.objects.filter(upload_file=upload_file))
            expires_at = upload_file.expires_at.timestamp()
            current_time = datetime.now().timestamp()
            if download_count < upload_file.max_downloads and expires_at > current_time:
                results = get_analytics(request.META)
                Analytics(upload_file_id=public_link, **results).save()
                return FileResponse(upload_file.file, as_attachment=True, filename=upload_file.file_name)
            else:
                # File expired
                raise Http404()
    return render(request, 'public_link.html', {"visible": visibility, "form": PasswordForm()})


def analytic_link_handle(request, analytic_link):
    """
    **Beta**
    Display various analytics for the public link such as:
        - OS family (Linux,Windows,Unix,etc)
        - Device type (Mobile,Tablet,Personal Computer)
        - Geolocation (Country,City,State)
        - DateTime public_link was downloaded
    :param request:
    :param analytic_link:
    :return:
    """
    query = UploadFiles.objects.all().filter(analytic_link=analytic_link)
    if query.exists() and query.first().expires_at.timestamp() > datetime.now().timestamp():
        results = Analytics.objects.filter(upload_file=query.first())
        chart_data = {}
        chart_attributes = ['os', 'device_type', 'browser']
        for data in chart_attributes:
            chart_data[data] = Counter([i[0] for i in list(results.values_list(data))]).items()
        detailed = list(
            results.values_list('os', 'device_type', 'browser', 'country', 'region', 'city', 'time_clicked'))
        return render(request, 'analytics.html', {"chart_data": chart_data, "detailed": detailed})
    else:
        raise Http404()
