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
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    rua = forms.CharField(
        label='Rua',
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    predio = forms.CharField(
        label='Prédio',
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    nivel = forms.CharField(
        label='Nível',
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    apto = forms.CharField(
        label='Apartamento',
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )

    def clean(self):
        dados = super().clean()
        if not dados.get('codigo', '').strip():
            estruturado = any(
                dados.get(campo, '').strip()
                for campo in ('rua', 'predio', 'nivel', 'apto')
            )
            if not estruturado:
                raise forms.ValidationError(
                    'Informe o código ou preencha Rua/Prédio/Nível/Apartamento.',
                )
        return dados


class PrecadastroPosicaoForm(forms.Form):
    codigo_completo = forms.CharField(
        label='Código completo da posição',
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    posicao_descricao = forms.CharField(
        label='Descrição da posição',
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    rua = forms.CharField(label='Rua', max_length=50, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    predio = forms.CharField(label='Prédio', max_length=50, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    nivel = forms.CharField(label='Nível', max_length=50, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    apto = forms.CharField(label='Apto', max_length=50, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    observacao = forms.CharField(
        label='Observação',
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
    )

    def clean(self):
        dados = super().clean()
        if not dados.get('codigo_completo', '').strip():
            estruturado = any(dados.get(campo, '').strip() for campo in ('rua', 'predio', 'nivel', 'apto'))
            if not estruturado:
                raise forms.ValidationError(
                    'Informe o código completo ou preencha Rua/Prédio/Nível/Apto.',
                )
        return dados


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
