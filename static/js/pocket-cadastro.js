/**
 * Pocket — pré-cadastro (produto e posição).
 * Não participa da contagem: sem debounce, sem mapas e sem storage.
 * O modo do teclado vive só nesta página (memória local, padrão físico).
 */
(function (global) {
    'use strict';

    var focoInicialFeito = false;

    function teclaEnter(evento) {
        return evento.key === 'Enter' || evento.key === 'NumpadEnter' ||
            evento.keyCode === 13 || evento.which === 13;
    }

    function campoDeTexto(campo) {
        if (!campo) return false;
        var tag = (campo.tagName || '').toUpperCase();
        if (tag === 'TEXTAREA') return true;
        if (tag !== 'INPUT') return false;
        var tipo = (campo.type || 'text').toLowerCase();
        return tipo === 'text' || tipo === 'search' || tipo === 'email' || tipo === 'tel' || tipo === 'url';
    }

    function editavel(campo) {
        return !!(campo && campoDeTexto(campo) && !campo.disabled && !campo.readOnly);
    }

    function camposDoFormulario(form) {
        return Array.prototype.filter.call(
            form.querySelectorAll('input, textarea'),
            campoDeTexto
        );
    }

    function manterVisivel(el) {
        if (!el || !el.getBoundingClientRect || !el.scrollIntoView) return;
        var rect = el.getBoundingClientRect();
        var vista = global.visualViewport;
        var topo = vista ? vista.offsetTop : 0;
        var altura = vista ? vista.height : global.innerHeight;
        var margem = 12;
        if (rect.top < topo + margem || rect.bottom > topo + altura - margem) {
            el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        }
    }

    function primeiroInvalido(form) {
        var grupos = form.querySelectorAll('.mb-3');
        var i;
        for (i = 0; i < grupos.length; i += 1) {
            if (!grupos[i].querySelector('.text-danger')) continue;
            var campo = grupos[i].querySelector('input, textarea');
            if (editavel(campo)) return campo;
        }
        return null;
    }

    function primeiroEditavel(form) {
        var campos = camposDoFormulario(form);
        var i;
        for (i = 0; i < campos.length; i += 1) {
            if (editavel(campos[i])) return campos[i];
        }
        return null;
    }

    function focarSeguinte(form, atual) {
        var campos = camposDoFormulario(form).filter(editavel);
        var indice = campos.indexOf(atual);
        var proximo = indice >= 0 ? campos[indice + 1] : null;
        if (proximo) {
            proximo.focus();
            manterVisivel(proximo);
            return;
        }
        var salvar = form.querySelector('button[type="submit"]');
        if (salvar && !salvar.disabled) {
            salvar.focus();
            manterVisivel(salvar);
        }
    }

    function aplicarModo(campos, virtual) {
        var modo = virtual ? 'text' : 'none';
        campos.forEach(function (campo) {
            campo.setAttribute('inputmode', modo);
        });
    }

    function atualizarBotao(botao, virtual) {
        botao.textContent = virtual ? 'Teclado: virtual' : 'Teclado: físico';
        botao.setAttribute('aria-pressed', virtual ? 'true' : 'false');
        botao.classList.toggle('is-virtual', virtual);
    }

    function configurarTeclado(botao, forms) {
        var virtual = false;
        var memoria = null;

        function camposAtivos() {
            var lista = [];
            Array.prototype.forEach.call(forms, function (form) {
                lista = lista.concat(camposDoFormulario(form));
            });
            return lista;
        }

        botao.addEventListener('pointerdown', function () {
            var ativo = global.document.activeElement;
            if (!ativo || !campoDeTexto(ativo)) {
                memoria = null;
                return;
            }
            memoria = {
                campo: ativo,
                inicio: typeof ativo.selectionStart === 'number' ? ativo.selectionStart : null,
                fim: typeof ativo.selectionEnd === 'number' ? ativo.selectionEnd : null,
            };
        });

        botao.addEventListener('mousedown', function (evento) {
            evento.preventDefault();
        });

        botao.addEventListener('click', function () {
            virtual = !virtual;
            aplicarModo(camposAtivos(), virtual);
            atualizarBotao(botao, virtual);
            var alvo = memoria && memoria.campo;
            if (!alvo || !global.document.contains(alvo)) return;
            alvo.focus();
            if (memoria.inicio !== null && typeof alvo.setSelectionRange === 'function') {
                try {
                    alvo.setSelectionRange(memoria.inicio, memoria.fim);
                } catch (erro) {
                    /* seleção indisponível neste campo */
                }
            }
        });
    }

    function configurarFormulario(form) {
        form.addEventListener('focusin', function (evento) {
            manterVisivel(evento.target);
        });

        form.addEventListener('keydown', function (evento) {
            if (!teclaEnter(evento)) return;
            var alvo = evento.target;
            if (!alvo || alvo.tagName === 'TEXTAREA' || alvo.tagName === 'BUTTON' || alvo.tagName === 'A') {
                return;
            }
            if (!campoDeTexto(alvo)) return;
            evento.preventDefault();
            if (alvo.getAttribute('data-cadastro-enter') === 'validar-codigo') return;
            focarSeguinte(form, alvo);
        });

        form.addEventListener('submit', function (evento) {
            if (evento.defaultPrevented) return;
            if (form.getAttribute('data-cadastro-enviando') === '1') {
                evento.preventDefault();
                return;
            }
            form.setAttribute('data-cadastro-enviando', '1');
            var botao = form.querySelector('button[type="submit"]');
            if (!botao) return;
            botao.setAttribute('aria-busy', 'true');
            global.setTimeout(function () {
                botao.disabled = true;
            }, 0);
        });

        if (focoInicialFeito) return;
        focoInicialFeito = true;
        var invalido = primeiroInvalido(form);
        var alvo = invalido || primeiroEditavel(form);
        if (!alvo) return;
        global.requestAnimationFrame(function () {
            var ativo = global.document.activeElement;
            if (ativo && ativo !== global.document.body && form.contains(ativo) && editavel(ativo)) {
                manterVisivel(ativo);
                return;
            }
            alvo.focus();
            if (invalido && alvo.tagName !== 'TEXTAREA' && typeof alvo.select === 'function') {
                alvo.select();
            }
            manterVisivel(alvo);
        });
    }

    function iniciar() {
        var forms = global.document.querySelectorAll('form.pocket-cadastro-form');
        if (!forms.length) return;
        var botao = global.document.getElementById('pocket-cadastro-teclado');
        if (botao) configurarTeclado(botao, forms);
        Array.prototype.forEach.call(forms, configurarFormulario);
        if (global.visualViewport) {
            global.visualViewport.addEventListener('resize', function () {
                var ativo = global.document.activeElement;
                if (ativo && campoDeTexto(ativo)) manterVisivel(ativo);
            });
        }
    }

    if (global.document.readyState === 'loading') {
        global.document.addEventListener('DOMContentLoaded', iniciar);
    } else {
        iniciar();
    }
}(window));
