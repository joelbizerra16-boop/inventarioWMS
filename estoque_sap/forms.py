from django import forms
from django.core.validators import FileExtensionValidator


class EstoqueSAPImportacaoForm(forms.Form):
    arquivo = forms.FileField(
        label='Arquivo Excel',
        validators=[FileExtensionValidator(allowed_extensions=['xlsx', 'xls'])],
        widget=forms.ClearableFileInput(attrs={
            'class': 'form-control',
            'accept': '.xlsx,.xls',
        }),
    )
