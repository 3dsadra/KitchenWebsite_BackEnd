from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib import messages 

def homepage(request):
    return render(request, "home.html") # Placeholder

def login_v(request):
    if request.method == 'POST':
        codemelli = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=codemelli, password=password)
        
        if user is not None:
            login(request, user)
            return redirect('/')
        else:
            messages.error(request, "اطلاعات وارد شده نادرست است.")
            return render(request, 'users/login.html')
    
    return render(request, 'users/login.html')

def logout_v(request):
    logout(request)
    messages.info(request, 'شما خارج شدید.')
    return redirect("/")
