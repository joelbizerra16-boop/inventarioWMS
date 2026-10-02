from django import forms
from django.core.validators import FileExtensionValidator
from django.db.models import Value
from django.db.models.functions import Lower, Replace, Trim

from produtos.models import Produto


def _embalagem_sem_branco(expressao='embalagem'):
    expr = expressao
    for caractere in ('\t', '\n', '\r', '\v', '\f', '\xa0'):
        expr = Replace(expr, Value(caractere), Value(''))
    return Trim(expr)


def listar_embalagens_distintas():
    """Valores distintos já gravados em Produto.embalagem, sem vazio nem só espaços."""
    return list(
        Produto.objects.exclude(embalagem__isnull=True)
        .annotate(_embalagem_util=_embalagem_sem_branco())
        .exclude(_embalagem_util='')
        .values_list('embalagem', flat=True)
        .distinct()
        .order_by(Lower('embalagem'), 'embalagem')
    )


class EmbalagemCatalogoFormMixin:
    """Select nativo com as embalagens que já existem. Não cria valor novo."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        opcoes = listar_embalagens_distintas()
        self.sem_embalagens = not opcoes
        escolhas = [('', 'Não informado')]
        escolhas.extend((valor, valor) for valor in opcoes)
        self.fields['embalagem'].choices = escolhas
        if self.sem_embalagens:
            self.fields['embalagem'].help_text = (
                'Nenhuma embalagem cadastrada nos produtos. '
                'Esta tela não cria uma embalagem nova.'
            )


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


class PrecadastroProdutoOperadorForm(EmbalagemCatalogoFormMixin, forms.Form):
    codigo_produto = forms.CharField(
        label='SKU',
        max_length=50,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
            'inputmode': 'none',
        }),
    )
    descricao = forms.CharField(
        label='Descrição',
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
            'inputmode': 'none',
        }),
    )
    embalagem = forms.ChoiceField(
        label='Embalagem',
        required=False,
        choices=[('', 'Não informado')],
        widget=forms.Select(attrs={'class': 'form-select pocket-input'}),
        error_messages={
            'invalid_choice': 'Selecione uma embalagem já cadastrada.',
        },
    )


class PrecadastroProdutoForm(EmbalagemCatalogoFormMixin, forms.Form):
    codigo_produto = forms.CharField(
        label='SKU',
        max_length=50,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
            'inputmode': 'none',
        }),
    )
    descricao = forms.CharField(
        label='Descrição',
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
            'inputmode': 'none',
        }),
    )
    codigo_ean = forms.CharField(
        label='EAN',
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
            'inputmode': 'none',
        }),
    )
    embalagem = forms.ChoiceField(
        label='Embalagem',
        required=False,
        choices=[('', 'Não informado')],
        widget=forms.Select(attrs={'class': 'form-select pocket-input'}),
        error_messages={
            'invalid_choice': 'Selecione uma embalagem já cadastrada.',
        },
    )
    observacao = forms.CharField(
        label='Observação',
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control pocket-input',
            'rows': 2,
            'autocomplete': 'off',
            'inputmode': 'none',
        }),
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
