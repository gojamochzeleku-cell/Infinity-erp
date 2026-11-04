from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

@login_required
def filing_portal(request):
    return render(request, 'filing_portal.html')

@login_required
def print_spine_labels(request):
    return render(request, 'print_spine_labels.html')

@login_required
def print_cabinet_labels(request):
    return render(request, 'print_cabinet_labels.html')

@login_required
def print_visual_map(request):
    return render(request, 'print_visual_map.html')
