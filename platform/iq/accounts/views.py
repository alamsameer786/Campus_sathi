from django import forms
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST


class SignupForm(forms.Form):
    username = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)
    password1 = forms.CharField(widget=forms.PasswordInput)
    password2 = forms.CharField(widget=forms.PasswordInput)

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('That username is taken.')
        return username

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip()
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def clean(self):
        cd = super().clean()
        p1, p2 = cd.get('password1'), cd.get('password2')
        if p1 and p2:
            if p1 != p2:
                self.add_error('password2', 'Passwords do not match.')
            else:
                try:
                    validate_password(p1, User(username=cd.get('username', ''), email=cd.get('email', '')))
                except forms.ValidationError as e:
                    self.add_error('password1', e)
        return cd


def _safe_next(request):
    nxt = request.POST.get('next') or request.GET.get('next') or ''
    ok = url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    return nxt if ok else ''


def signup_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    form = SignupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        cd = form.cleaned_data
        user = User.objects.create_user(cd['username'], cd['email'], cd['password1'])
        login(request, user)
        messages.success(request, f'Welcome to Campus IQ, {user.username}!')
        return redirect(_safe_next(request) or 'home')
    return render(request, 'accounts/signup.html', {'form': form, 'next': _safe_next(request)})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        login(request, form.get_user())
        return redirect(_safe_next(request) or 'home')
    return render(request, 'accounts/login.html', {'form': form, 'next': _safe_next(request)})


@require_POST
def logout_view(request):
    logout(request)
    messages.success(request, 'You have been logged out.')
    return redirect('home')
