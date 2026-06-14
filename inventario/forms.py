from django import forms

from inventario.models import CicloInventarioSku, Inventario, InventarioItem
from inventario.services.ciclico import CiclicoError
from inventario.services.pocket_ciclico_fila import validar_codigo_produto_lote_ciclico
from inventario.services.pocket_mestres import obter_mapas_mestres_pocket
from posicoes.models import Posicao
from produtos.models import Produto


class InventarioForm(forms.ModelForm):
    class Meta:
        model = Inventario
        fields = ['usuario', 'observacao']
        widgets = {
            'usuario': forms.Select(attrs={'class': 'form-select'}),
            'observacao': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4,
            }),
        }


class ContagemForm(forms.ModelForm):
    class Meta:
        model = InventarioItem
        fields = ['posicao', 'produto', 'quantidade_fisica']
        widgets = {
            'posicao': forms.Select(attrs={'class': 'form-select'}),
            'produto': forms.Select(attrs={'class': 'form-select'}),
            'quantidade_fisica': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.001',
                'min': '0',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Posicao.objects.filter(ativo=True).order_by('codigo')
        self.fields['posicao'].queryset = queryset
        self.fields['posicao'].label = 'Selecionar posição'
        self.fields['posicao'].label_from_instance = (
            lambda posicao: f'{posicao.codigo} | {posicao.posicao}'
        )
        self.posicoes_alocacao = {
            str(posicao.pk): {
                'codigo': posicao.codigo,
                'alocacao': posicao.posicao,
            }
            for posicao in queryset
        }
        self.fields['produto'].queryset = Produto.objects.filter(
            ativo=True,
        ).order_by('codigo_produto')


class PocketContagemForm(forms.Form):
    codigo_posicao = forms.CharField(
        label='Código da Posição',
        max_length=50,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
            'autocorrect': 'off',
            'autocapitalize': 'off',
            'spellcheck': 'false',
            'inputmode': 'text',
            'placeholder': 'Bipar posição',
        }),
    )
    codigo_produto = forms.CharField(
        label='Produto ou EAN',
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
            'autocorrect': 'off',
            'autocapitalize': 'off',
            'spellcheck': 'false',
            'inputmode': 'text',
            'placeholder': 'Bipar produto ou EAN',
        }),
    )
    quantidade_fisica = forms.IntegerField(
        label='Quantidade',
        min_value=1,
        error_messages={
            'required': 'Quantidade obrigatória.',
            'invalid': 'Quantidade inválida.',
            'min_value': 'Quantidade deve ser maior que zero.',
        },
        widget=forms.NumberInput(attrs={
            'class': 'form-control pocket-input',
            'step': '1',
            'min': '1',
            'inputmode': 'numeric',
        }),
    )
    dispositivo = forms.CharField(
        label='Dispositivo',
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg pocket-input',
            'autocomplete': 'off',
            'placeholder': 'Ex.: Coletor 03',
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        mapas = obter_mapas_mestres_pocket()
        self.mapa_posicoes = mapas['mapa_posicoes']
        self.mapa_produtos = mapas['mapa_produtos']
        self.mapa_ean = mapas['mapa_ean']
        self.mapa_embalagens = mapas['mapa_embalagens']

    def clean_codigo_produto(self):
        codigo = self.cleaned_data.get('codigo_produto', '').strip()
        if not codigo:
            raise forms.ValidationError('Informe o código do produto ou EAN.')
        return codigo


class PocketContagemCiclicoForm(forms.Form):
    sku_id = forms.ChoiceField(
        label='Produtos pendentes do ciclo',
        choices=[],
        widget=forms.Select(attrs={
            'class': 'form-select pocket-input pocket-sku-combobox',
            'id': 'pocket-sku-lote',
        }),
    )
    codigo_posicao = forms.CharField(
        label='Código da Posição',
        max_length=50,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
            'autocorrect': 'off',
            'autocapitalize': 'off',
            'spellcheck': 'false',
            'inputmode': 'text',
            'placeholder': 'Bipar posição',
        }),
    )
    quantidade_fisica = forms.IntegerField(
        label='Quantidade',
        min_value=1,
        error_messages={
            'required': 'Quantidade obrigatória.',
            'invalid': 'Quantidade inválida.',
            'min_value': 'Quantidade deve ser maior que zero.',
        },
        widget=forms.NumberInput(attrs={
            'class': 'form-control pocket-input',
            'step': '1',
            'min': '1',
            'inputmode': 'numeric',
            'disabled': 'disabled',
        }),
    )
    codigo_produto_lido = forms.CharField(
        label='Produto ou EAN',
        max_length=50,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input pocket-input--readonly',
            'id': 'pocket-produto-ciclico',
            'autocomplete': 'off',
            'autocorrect': 'off',
            'autocapitalize': 'off',
            'spellcheck': 'false',
            'inputmode': 'text',
            'placeholder': 'Confirmado após a posição',
            'disabled': 'disabled',
        }),
    )

    def __init__(self, *args, fila=None, **kwargs):
        super().__init__(*args, **kwargs)
        fila = fila or []
        self.fields['sku_id'].choices = [
            (str(item.pk), f'{item.codigo_produto} - {item.descricao}')
            for item in fila
        ]
        mapas = obter_mapas_mestres_pocket()
        self.mapa_posicoes = mapas['mapa_posicoes']
        self.mapa_produtos = mapas['mapa_produtos']
        self.mapa_ean = mapas['mapa_ean']

    def clean_sku_id(self):
        valor = self.cleaned_data.get('sku_id', '').strip()
        if not valor:
            raise forms.ValidationError('Selecione um SKU do lote.')
        return int(valor)

    def clean(self):
        cleaned_data = super().clean()
        sku_id = cleaned_data.get('sku_id')
        codigo_lido = (cleaned_data.get('codigo_produto_lido') or '').strip()
        if sku_id is None:
            return cleaned_data
        if not codigo_lido:
            self.add_error('codigo_produto_lido', 'Informe o produto ou EAN.')
            return cleaned_data
        sku = CicloInventarioSku.objects.select_related('produto').filter(pk=sku_id).first()
        if sku is None:
            self.add_error('sku_id', 'SKU inválido.')
            return cleaned_data
        try:
            validar_codigo_produto_lote_ciclico(sku, codigo_lido)
        except CiclicoError as exc:
            self.add_error('codigo_produto_lido', str(exc))
        return cleaned_data
