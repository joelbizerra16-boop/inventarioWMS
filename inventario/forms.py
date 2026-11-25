from django import forms
from django.db.models import Q

from inventario.models import CicloInventarioSku, Inventario, InventarioItem
from inventario.services.ciclico import CiclicoError
from inventario.services.pocket_ciclico_fila import validar_codigo_produto_lote_ciclico
from inventario.services.pocket_mestres import obter_mapas_mestres_pocket
from posicoes.models import Posicao
from produtos.models import Produto


def inventario_permite_trocar_categoria(inventario) -> bool:
    """A frente só pode mudar antes do início da contagem.

    Bloqueia se houver contagem, tarefa, lock, inclusão explícita ou
    associação a conferência do Comparativo.
    """
    if not inventario.pk:
        return True
    if inventario.status != Inventario.Status.ABERTO:
        return False
    return not (
        inventario.itens.exists()
        or inventario.tarefas.exists()
        or inventario.locks.exists()
        or inventario.produtos_incluidos.exists()
        or inventario.associacoes_comparativo.exists()
    )


class InventarioForm(forms.ModelForm):
    ROTULOS_FRENTE = {
        valor: f'Inventário de {rotulo}' for valor, rotulo in Inventario._meta.get_field('categoria').choices
    }

    categoria = forms.ChoiceField(
        label='Frente do inventário',
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
        error_messages={'required': 'Escolha a frente: Lubrificantes, Filtros ou Agregados.'},
    )

    class Meta:
        model = Inventario
        fields = ['usuario', 'categoria', 'observacao']
        widgets = {
            'usuario': forms.Select(attrs={'class': 'form-select'}),
            'observacao': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4,
            }),
        }

    def __init__(self, *args, categoria_inicial=None, **kwargs):
        super().__init__(*args, **kwargs)
        campo = self.fields['categoria']
        campo.choices = list(self.ROTULOS_FRENTE.items())
        if self.instance.pk:
            campo.initial = self.instance.categoria
            self.categoria_bloqueada = not inventario_permite_trocar_categoria(self.instance)
            if self.categoria_bloqueada:
                # Contagem iniciada: a frente gravada é definitiva (POST manual ignorado).
                campo.disabled = True
                campo.required = False
                campo.help_text = 'Contagem iniciada: a frente gravada não pode mais ser alterada.'
        else:
            self.categoria_bloqueada = False
            campo.initial = categoria_inicial

    def clean_categoria(self):
        if self.categoria_bloqueada:
            return self.instance.categoria
        return self.cleaned_data['categoria']


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

    def __init__(self, *args, inventario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.inventario = inventario
        queryset = Posicao.objects.filter(ativo=True)
        if inventario is not None:
            # Somente posições da categoria gravada no inventário; a posição de
            # um lançamento histórico em edição continua disponível.
            escopo = Q(categoria=inventario.categoria)
            if self.instance.pk and self.instance.posicao_id:
                escopo |= Q(pk=self.instance.posicao_id)
            queryset = queryset.filter(escopo)
        queryset = queryset.order_by('codigo')
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
        produtos = Produto.objects.elegiveis_para_inventario()
        if inventario is not None:
            # Produtos da categoria do inventário + sem classificação incluídos
            # explicitamente. O backend (salvar_contagem) valida de novo.
            from inventario.services.categoria import q_produtos_contaveis_no_inventario

            escopo = q_produtos_contaveis_no_inventario(inventario)
            if self.instance.pk and self.instance.produto_id:
                escopo |= Q(pk=self.instance.produto_id)
            produtos = produtos.filter(escopo)
        self.fields['produto'].queryset = produtos.order_by('codigo_produto')

    def clean_quantidade_fisica(self):
        quantidade = self.cleaned_data.get('quantidade_fisica')
        if quantidade is not None and quantidade < 0:
            raise forms.ValidationError('Quantidade não pode ser negativa.')
        return quantidade


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
            'autofocus': True,
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
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
            'autocorrect': 'off',
            'spellcheck': 'false',
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

    def __init__(self, *args, inventario=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Com inventário: mapas restritos à categoria gravada nele.
        mapas = obter_mapas_mestres_pocket(inventario)
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
            'autofocus': True,
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
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
            'autocorrect': 'off',
            'spellcheck': 'false',
            'inputmode': 'numeric',
        }),
    )
    codigo_produto_lido = forms.CharField(
        label='Produto ou EAN',
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'id': 'pocket-produto-ciclico',
            'autocomplete': 'off',
            'autocorrect': 'off',
            'autocapitalize': 'off',
            'spellcheck': 'false',
            'placeholder': 'Bipar produto ou EAN',
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
        sku = CicloInventarioSku.objects.select_related('produto').filter(pk=sku_id).first()
        if sku is None:
            self.add_error('sku_id', 'SKU inválido.')
            return cleaned_data
        if not codigo_lido:
            codigo_lido = sku.codigo_produto
            cleaned_data['codigo_produto_lido'] = codigo_lido
        try:
            validar_codigo_produto_lote_ciclico(sku, codigo_lido)
        except CiclicoError as exc:
            self.add_error('codigo_produto_lido', str(exc))
        return cleaned_data
