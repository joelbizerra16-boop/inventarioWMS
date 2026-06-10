from django import forms

from inventario.models import Inventario, InventarioItem
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
            'class': 'form-control form-control-lg pocket-input',
            'autocomplete': 'off',
            'autofocus': True,
            'inputmode': 'text',
            'placeholder': 'Bipar ou digitar posição',
        }),
    )
    codigo_produto = forms.CharField(
        label='Código Produto / EAN',
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg pocket-input',
            'autocomplete': 'off',
            'inputmode': 'text',
            'placeholder': 'Bipar ou digitar produto',
        }),
    )
    quantidade_fisica = forms.IntegerField(
        label='Quantidade',
        min_value=0,
        widget=forms.NumberInput(attrs={
            'class': 'form-control form-control-lg pocket-input',
            'step': '1',
            'min': '0',
            'inputmode': 'numeric',
            'placeholder': '0',
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
        self.mapa_posicoes = {
            posicao['codigo']: posicao['posicao']
            for posicao in Posicao.objects.filter(ativo=True).values('codigo', 'posicao')
        }
        self.mapa_produtos = {}
        self.mapa_ean = {}
        for produto in Produto.objects.filter(ativo=True).values(
            'codigo_produto',
            'descricao',
            'codigo_ean',
        ):
            self.mapa_produtos[produto['codigo_produto']] = produto['descricao']
            if produto['codigo_ean']:
                self.mapa_ean[produto['codigo_ean']] = {
                    'descricao': produto['descricao'],
                    'codigo_produto': produto['codigo_produto'],
                }

    def clean_codigo_produto(self):
        codigo = self.cleaned_data.get('codigo_produto', '').strip()
        if not codigo:
            raise forms.ValidationError('Informe o código do produto ou EAN.')
        return codigo


class PocketContagemCiclicoForm(forms.Form):
    sku_id = forms.ChoiceField(
        label='SKU do Lote',
        choices=[],
        widget=forms.Select(attrs={
            'class': 'form-select form-select-lg pocket-input',
            'id': 'pocket-sku-lote',
        }),
    )
    codigo_posicao = forms.CharField(
        label='Código da Posição',
        max_length=50,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg pocket-input',
            'autocomplete': 'off',
            'autofocus': True,
            'inputmode': 'text',
            'placeholder': 'Bipar ou digitar posição',
        }),
    )
    quantidade_fisica = forms.IntegerField(
        label='Quantidade',
        min_value=0,
        widget=forms.NumberInput(attrs={
            'class': 'form-control form-control-lg pocket-input',
            'step': '1',
            'min': '0',
            'inputmode': 'numeric',
            'placeholder': '0',
        }),
    )

    def __init__(self, *args, fila=None, **kwargs):
        super().__init__(*args, **kwargs)
        fila = fila or []
        self.fields['sku_id'].choices = [
            (str(item.pk), f'{item.codigo_produto} — {item.descricao}')
            for item in fila
        ]
        self.mapa_posicoes = {
            posicao['codigo']: posicao['posicao']
            for posicao in Posicao.objects.filter(ativo=True).values('codigo', 'posicao')
        }

    def clean_sku_id(self):
        valor = self.cleaned_data.get('sku_id', '').strip()
        if not valor:
            raise forms.ValidationError('Selecione um SKU do lote.')
        return int(valor)
