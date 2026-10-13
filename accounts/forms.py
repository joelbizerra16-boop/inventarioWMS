from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from accounts.models import Usuario


class OperacionalAuthenticationForm(AuthenticationForm):
    """Login operacional. A senha é comparada exatamente como foi digitada."""

    error_messages = {
        **AuthenticationForm.error_messages,
        'inactive': 'Usuário inativo. Contate o suporte.',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'Usuário'
        self.fields['username'].error_messages['required'] = 'Informe o usuário.'
        self.fields['username'].widget.attrs.update({
            'class': 'form-control',
            'autocapitalize': 'none',
            'autocorrect': 'off',
            'spellcheck': 'false',
            'autocomplete': 'username',
            'autofocus': True,
        })
        self.fields['password'].strip = False
        self.fields['password'].label = 'Senha'
        self.fields['password'].error_messages['required'] = 'Informe a senha.'
        self.fields['password'].widget.attrs.update({
            'class': 'form-control',
            'autocapitalize': 'none',
            'autocorrect': 'off',
            'spellcheck': 'false',
            'autocomplete': 'current-password',
        })

    def get_invalid_login_error(self):
        return ValidationError('Usuário ou senha inválidos.', code='invalid_login')

    def confirm_login_allowed(self, user):
        # ModelBackend já recusa is_active=False antes desta checagem.
        # Se um backend devolver o usuário inativo, a mensagem continua distinta.
        try:
            super().confirm_login_allowed(user)
        except ValidationError as exc:
            if getattr(exc, 'code', None) == 'inactive':
                raise ValidationError(
                    self.error_messages['inactive'],
                    code='inactive',
                ) from exc
            raise


PERFIS_FORMULARIO = (
    Usuario.Perfil.ADMINISTRADOR,
    Usuario.Perfil.SUPERVISOR,
    Usuario.Perfil.OPERADOR,
    Usuario.Perfil.CONSULTA,
)


class UsuarioForm(forms.ModelForm):
    senha = forms.CharField(
        label='Senha',
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'autocomplete': 'new-password',
        }),
    )
    confirmar_senha = forms.CharField(
        label='Confirmar senha',
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'autocomplete': 'new-password',
        }),
    )

    class Meta:
        model = Usuario
        fields = ['nome', 'login', 'setor', 'perfil', 'ativo']
        widgets = {
            'nome': forms.TextInput(attrs={'class': 'form-control'}),
            'login': forms.TextInput(attrs={'class': 'form-control'}),
            'setor': forms.TextInput(attrs={'class': 'form-control'}),
            'perfil': forms.Select(attrs={'class': 'form-select'}),
            'ativo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        self.criacao = kwargs.pop('criacao', False)
        super().__init__(*args, **kwargs)
        self.fields['perfil'].choices = [
            (valor, rotulo)
            for valor, rotulo in Usuario.Perfil.choices
            if valor in PERFIS_FORMULARIO or (
                self.instance.pk and self.instance.perfil == valor
            )
        ]
        if self.criacao:
            self.fields['senha'].required = True
            self.fields['confirmar_senha'].required = True

    def clean_login(self):
        login = (self.cleaned_data.get('login') or '').strip()
        if not login:
            raise forms.ValidationError('Informe o login.')
        return login

    def clean(self):
        cleaned = super().clean()
        senha = cleaned.get('senha') or ''
        confirmar = cleaned.get('confirmar_senha') or ''

        if self.criacao or senha or confirmar:
            if len(senha) < 8:
                self.add_error('senha', 'A senha deve ter pelo menos 8 caracteres.')
            if senha != confirmar:
                self.add_error('confirmar_senha', 'As senhas não conferem.')

        return cleaned
