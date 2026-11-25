from core.categorias import obter_categoria_sessao, opcoes_categoria, rotulo_categoria


def categoria_inventario(request):
    """Categoria selecionada na sessão (Lubrificantes por padrão) e opções do seletor."""
    categoria = obter_categoria_sessao(request)
    return {
        'categoria_atual': categoria,
        'categoria_atual_rotulo': rotulo_categoria(categoria),
        'categorias_opcoes': opcoes_categoria(),
    }
