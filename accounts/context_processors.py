from accounts.services.perfil import usuario_e_operador_pocket


def perfil_operacional(request):
    usuario = request.user
    operador = usuario.is_authenticated and usuario_e_operador_pocket(usuario)
    return {
        'perfil_operador_pocket': operador,
        'base_template': 'base_operador.html' if operador else 'base.html',
    }
