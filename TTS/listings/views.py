from django.shortcuts import render
from django.http import HttpResponse
from .form import prompt_Form

# Create your views here.
def index(request):
    if request.method == 'POST':
        form = prompt_Form(request.POST)
        if form.is_valid():
            # Process the form data
            pass
    else:
        form = prompt_Form()
    return render(request, 'listings/index.html', {'form': form})