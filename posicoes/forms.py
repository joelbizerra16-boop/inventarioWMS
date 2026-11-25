from django import forms
from django.core.validators import FileExtensionValidator

from core.categorias import CATEGORIA_CHOICES
from posicoes.models import Posicao


def campo_categoria_obrigatorio(initial=None) -> forms.ChoiceField:
    return forms.ChoiceField(
        label='Área de inventário',
        choices=[('', 'Selecione a categoria')] + list(CATEGORIA_CHOICES),
        required=True,
        initial=initial,
        error_messages={'required': 'Informe a área de inventário (categoria) da posição.'},
        widget=forms.Select(attrs={'class': 'form-select'}),
    )


def mensagem_codigo_existente(existente: Posicao, categoria_informada: str | None = None) -> str:
    if categoria_informada and existente.categoria != categoria_informada:
        return (
            f'O código {existente.codigo} já pertence a uma posição de '
            f'{existente.categoria_rotulo}. Cada código pertence a uma única categoria.'
        )
    return f'O código {existente.codigo} já está cadastrado ({existente.categoria_rotulo}).'


def motivo_bloqueio_troca_categoria(posicao: Posicao) -> str:
    """Impede mover de categoria posição com saldo físico ou contagem em aberto."""
    from estoque_fisico.models import EstoqueFisico
    from inventario.models import Inventario, InventarioItem

    if EstoqueFisico.objects.filter(posicao=posicao).exists():
        return (
            'A posição possui estoque físico publicado em '
            f'{posicao.categoria_rotulo}; a área de inventário não pode ser alterada.'
        )
    if InventarioItem.objects.filter(posicao=posicao).exclude(
        inventario__status=Inventario.Status.FINALIZADO,
    ).exists():
        return (
            'A posição possui contagens em inventário não finalizado; '
            'a área de inventário não pode ser alterada.'
        )
    return ''


class PosicaoForm(forms.ModelForm):
    class Meta:
        model = Posicao
        fields = ['codigo', 'posicao', 'categoria', 'ativo']
        widgets = {
            'codigo': forms.TextInput(attrs={'class': 'form-control'}),
            'posicao': forms.TextInput(attrs={'class': 'form-control'}),
            'ativo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Nova posição exige escolha explícita (sem pré-seleção do default).
        inicial = self.instance.categoria if self.instance.pk else None
        self.fields['categoria'] = campo_categoria_obrigatorio(initial=inicial)

    def clean(self):
        dados = super().clean()
        codigo = (dados.get('codigo') or '').strip()
        categoria = dados.get('categoria')
        if codigo:
            existente = Posicao.objects.filter(codigo__iexact=codigo).exclude(pk=self.instance.pk).first()
            if existente is not None:
                # Campo com erro fica fora da checagem genérica de unicidade.
                self.add_error('codigo', mensagem_codigo_existente(existente, categoria))
        if self.instance.pk and categoria and categoria != self.instance.categoria:
            motivo = motivo_bloqueio_troca_categoria(self.instance)
            if motivo:
                self.add_error('categoria', motivo)
        return dados


class PrecadastroPosicaoOperadorForm(forms.Form):
    codigo = forms.CharField(
        label='Código',
        max_length=50,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
            'autocorrect': 'off',
            'autocapitalize': 'off',
            'spellcheck': 'false',
            'autofocus': True,
            'data-cadastro-enter': 'validar-codigo',
        }),
    )
    posicao = forms.CharField(
        label='Posição',
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
        }),
    )
    # Operador fora de um inventário escolhe a área explicitamente (vem
    # preenchida com a categoria selecionada na sessão).
    categoria = forms.ChoiceField(
        label='Área de inventário',
        choices=[('', 'Selecione a categoria')] + list(CATEGORIA_CHOICES),
        error_messages={'required': 'Informe a área de inventário (categoria) da posição.'},
        widget=forms.Select(attrs={'class': 'form-select form-select-lg pocket-input'}),
    )


class PrecadastroPosicaoForm(forms.Form):
    codigo = forms.CharField(
        label='Código',
        max_length=50,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
            'autocorrect': 'off',
            'autocapitalize': 'off',
            'spellcheck': 'false',
            'autofocus': True,
            'data-cadastro-enter': 'validar-codigo',
        }),
    )
    posicao = forms.CharField(
        label='Posição',
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control pocket-input',
            'autocomplete': 'off',
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
            'categoria',
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['categoria'] = campo_categoria_obrigatorio(initial=self.instance.categoria)

    def clean(self):
        dados = super().clean()
        codigo = (dados.get('codigo') or '').strip()
        categoria = dados.get('categoria')
        if codigo:
            existente = Posicao.objects.filter(codigo__iexact=codigo).exclude(pk=self.instance.pk).first()
            if existente is not None:
                self.add_error('codigo', mensagem_codigo_existente(existente, categoria))
        if self.instance.pk and categoria and categoria != self.instance.categoria:
            motivo = motivo_bloqueio_troca_categoria(self.instance)
            if motivo:
                self.add_error('categoria', motivo)
        return dados


class PosicaoImportacaoForm(forms.Form):
    arquivo = forms.FileField(
        label='Arquivo Excel',
        validators=[FileExtensionValidator(allowed_extensions=['xlsx', 'xls'])],
        widget=forms.ClearableFileInput(attrs={
            'class': 'form-control',
            'accept': '.xlsx,.xls',
        }),
    )
