from django import forms
from django.core.validators import FileExtensionValidator

from posicoes.models import Posicao


class PosicaoForm(forms.ModelForm):
    class Meta:
        model = Posicao
        fields = ['codigo', 'posicao', 'ativo']
        widgets = {
            'codigo': forms.TextInput(attrs={'class': 'form-control'}),
            'posicao': forms.TextInput(attrs={'class': 'form-control'}),
            'ativo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class PrecadastroPosicaoOperadorForm(forms.Form):
    codigo = forms.CharField(
        label='Código',
        max_length=50,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
            'inputmode': 'text',
        }),
    )
    posicao = forms.CharField(
        label='Posição',
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
            'inputmode': 'text',
        }),
    )


class PrecadastroPosicaoForm(forms.Form):
    codigo = forms.CharField(
        label='Código',
        max_length=50,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
            'inputmode': 'text',
        }),
    )
    posicao = forms.CharField(
        label='Posição',
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
            'inputmode': 'text',
        }),
    )


class PosicaoHomologacaoForm(forms.ModelForm):
    aprovar = forms.BooleanField(
        label='Aprovar após salvar',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    class Meta:
        model = Posicao
        fields = [
            'codigo',
            'posicao',
            'rua',
            'predio',
            'nivel',
            'apto',
            'observacao_precadastro',
        ]
        widgets = {
            'codigo': forms.TextInput(attrs={'class': 'form-control'}),
            'posicao': forms.TextInput(attrs={'class': 'form-control'}),
            'rua': forms.TextInput(attrs={'class': 'form-control'}),
            'predio': forms.TextInput(attrs={'class': 'form-control'}),
            'nivel': forms.TextInput(attrs={'class': 'form-control'}),
            'apto': forms.TextInput(attrs={'class': 'form-control'}),
            'observacao_precadastro': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class PosicaoImportacaoForm(forms.Form):
    arquivo = forms.FileField(
        label='Arquivo Excel',
        validators=[FileExtensionValidator(allowed_extensions=['xlsx', 'xls'])],
        widget=forms.ClearableFileInput(attrs={
            'class': 'form-control',
            'accept': '.xlsx,.xls',
        }),
    )
