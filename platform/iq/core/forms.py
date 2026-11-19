from django import forms
from django.contrib.auth.models import User


class AccountForm(forms.Form):
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False)
    university = forms.CharField(max_length=150, required=False)
    target_exam = forms.CharField(max_length=150, required=False)
    bio = forms.CharField(max_length=500, required=False)
    avatar = forms.ImageField(required=False)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip()
        if email and User.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError('That email is already used by another account.')
        return email
