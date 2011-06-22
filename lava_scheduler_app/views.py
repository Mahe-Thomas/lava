from django.template import RequestContext
from django.shortcuts import render_to_response

from lava_scheduler_app.models import Device

def index(request):
    print Device.objects.all()
    return render_to_response(
        "lava_scheduler_app/index.html",
        {
            'devices': Device.objects.all(),
        },
        RequestContext(request))
