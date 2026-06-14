from django import forms
from django.core.validators import FileExtensionValidator

from produtos.models import Produto


class ProdutoForm(forms.ModelForm):
    class Meta:
        model = Produto
        fields = [
            'codigo_produto',
            'descricao',
            'embalagem',
            'setor',
            'codigo_ean',
            'ativo',
        ]
        widgets = {
            'codigo_produto': forms.TextInput(attrs={'class': 'form-control'}),
            'descricao': forms.TextInput(attrs={'class': 'form-control'}),
            'embalagem': forms.TextInput(attrs={
                'class': 'form-control',
                'list': 'embalagem-opcoes',
                'autocomplete': 'off',
            }),
            'setor': forms.TextInput(attrs={
                'class': 'form-control',
                'list': 'setor-opcoes',
                'autocomplete': 'off',
            }),
            'codigo_ean': forms.TextInput(attrs={'class': 'form-control'}),
            'ativo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.opcoes_embalagem = list(
            Produto.objects
            .exclude(embalagem='')
            .exclude(embalagem__isnull=True)
            .values_list('embalagem', flat=True)
            .distinct()
            .order_by('embalagem')
        )
        self.opcoes_setor = list(
            Produto.objects
            .exclude(setor='')
            .exclude(setor__isnull=True)
            .values_list('setor', flat=True)
            .distinct()
            .order_by('setor')
        )


class PrecadastroProdutoOperadorForm(forms.Form):
    codigo_produto = forms.CharField(
        label='SKU',
        max_length=50,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    descricao = forms.CharField(
        label='Descrição',
        max_length=255,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    embalagem = forms.CharField(
        label='Embalagem',
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )


class PrecadastroProdutoForm(forms.Form):
    codigo_produto = forms.CharField(
        label='SKU',
        max_length=50,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    descricao = forms.CharField(
        label='Descrição',
        max_length=255,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    codigo_ean = forms.CharField(
        label='EAN',
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    embalagem = forms.CharField(
        label='Embalagem',
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    observacao = forms.CharField(
        label='Observação',
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
    )


class ProdutoHomologacaoForm(forms.ModelForm):
    aprovar = forms.BooleanField(
        label='Aprovar após salvar',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    class Meta:
        model = Produto
        fields = [
            'codigo_produto',
            'descricao',
            'embalagem',
            'setor',
            'codigo_ean',
            'observacao_precadastro',
        ]
        widgets = {
            'codigo_produto': forms.TextInput(attrs={'class': 'form-control'}),
            'descricao': forms.TextInput(attrs={'class': 'form-control'}),
            'embalagem': forms.TextInput(attrs={'class': 'form-control'}),
            'setor': forms.TextInput(attrs={'class': 'form-control'}),
            'codigo_ean': forms.TextInput(attrs={'class': 'form-control'}),
            'observacao_precadastro': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class ProdutoImportacaoForm(forms.Form):
    arquivo = forms.FileField(
        label='Arquivo Excel',
        validators=[FileExtensionValidator(allowed_extensions=['xlsx', 'xls'])],
        widget=forms.ClearableFileInput(attrs={
            'class': 'form-control',
            'accept': '.xlsx,.xls',
        }),
    )
