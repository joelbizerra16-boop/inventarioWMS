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

    function campoSelecao(campo) {
        if (!campo) return false;
        return (campo.tagName || '').toUpperCase() === 'SELECT' && !campo.disabled;
    }

    function campoNavegavel(campo) {
        return editavel(campo) || campoSelecao(campo);
    }

    function camposDoFormulario(form) {
        return Array.prototype.filter.call(
            form.querySelectorAll('input, textarea'),
            campoDeTexto
        );
    }

    function camposNavegaveis(form) {
        return Array.prototype.filter.call(
            form.querySelectorAll('input, textarea, select'),
            campoNavegavel
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
            var campo = grupos[i].querySelector('input, textarea, select');
            if (campoNavegavel(campo)) return campo;
        }
        return null;
    }

    function primeiroEditavel(form) {
        var campos = camposNavegaveis(form);
        var i;
        for (i = 0; i < campos.length; i += 1) {
            if (campoNavegavel(campos[i])) return campos[i];
        }
        return null;
    }

    function focarSeguinte(form, atual) {
        var campos = camposNavegaveis(form);
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

    function instalarRecepcaoDeLeitura(campo) {
        if (!campo || campo.getAttribute('data-pocket-recepcao') === '1') return;
        campo.setAttribute('data-pocket-recepcao', '1');
        var substituindo = false;
        var viaTecla = false;

        function limite() {
            var bruto = campo.getAttribute('maxlength');
            if (!bruto) return 0;
            var n = parseInt(bruto, 10);
            return n > 0 ? n : 0;
        }

        function aplicar(novo, cursor) {
            var max = limite();
            if (max && novo.length > max) {
                novo = novo.slice(0, max);
                if (cursor > novo.length) cursor = novo.length;
            }
            campo.value = novo;
            try {
                campo.setSelectionRange(cursor, cursor);
            } catch (_e) { /* campo sem seleção */ }
            campo.dispatchEvent(new Event('input', { bubbles: true }));
        }

        function selecionouTudo() {
            var valor = campo.value || '';
            if (!valor.length) return false;
            var inicio = campo.selectionStart;
            var fim = campo.selectionEnd;
            return inicio === 0 && fim === valor.length;
        }

        function inserir(texto) {
            var valor = campo.value || '';
            if (substituindo) {
                var acrescentado = valor + texto;
                aplicar(acrescentado, acrescentado.length);
                return;
            }
            substituindo = true;
            aplicar(texto, texto.length);
        }

        function soltarSubstituicao(evento) {
            if (evento.ctrlKey || evento.altKey || evento.metaKey) {
                substituindo = false;
                return true;
            }
            var key = evento.key;
            if (!key || key === 'Enter' || key === 'NumpadEnter' || key === 'Tab' ||
                key === 'Backspace' || key === 'Delete' ||
                key === 'ArrowLeft' || key === 'ArrowRight' ||
                key === 'ArrowUp' || key === 'ArrowDown' ||
                key === 'Home' || key === 'End' || key === 'Escape') {
                substituindo = false;
                return true;
            }
            return false;
        }

        campo.addEventListener('focus', function () {
            substituindo = false;
        });
        campo.addEventListener('pointerdown', function () {
            substituindo = false;
        });
        campo.addEventListener('compositionstart', function () {
            substituindo = false;
        });
        campo.addEventListener('keydown', function (evento) {
            viaTecla = false;
            if (evento.isComposing || evento.key === 'Process' || evento.key === 'Unidentified') {
                return;
            }
            if (soltarSubstituicao(evento)) return;
            if (!evento.key || evento.key.length !== 1) return;
            if (!substituindo && !selecionouTudo()) return;
            evento.preventDefault();
            viaTecla = true;
            inserir(evento.key);
        }, true);
        campo.addEventListener('beforeinput', function (evento) {
            if (viaTecla) {
                viaTecla = false;
                return;
            }
            if (evento.isComposing || evento.defaultPrevented) return;
            if (!evento.data || evento.data.length !== 1) return;
            if (evento.inputType && evento.inputType !== 'insertText') return;
            if (!substituindo && !selecionouTudo()) return;
            evento.preventDefault();
            inserir(evento.data);
        });
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
        camposDoFormulario(form).forEach(instalarRecepcaoDeLeitura);

        form.addEventListener('focusin', function (evento) {
            manterVisivel(evento.target);
        });

        form.addEventListener('keydown', function (evento) {
            if (!teclaEnter(evento)) return;
            var alvo = evento.target;
            if (!alvo || alvo.tagName === 'TEXTAREA' || alvo.tagName === 'BUTTON' || alvo.tagName === 'A') {
                return;
            }
            if (!campoDeTexto(alvo) && !campoSelecao(alvo)) return;
            evento.preventDefault();
            if (alvo.getAttribute('data-cadastro-enter') === 'validar-codigo') return;
            alvo.setAttribute('data-cadastro-enter-pendente', '1');
        });

        form.addEventListener('keyup', function (evento) {
            if (!teclaEnter(evento)) return;
            var alvo = evento.target;
            if (!alvo || alvo.getAttribute('data-cadastro-enter-pendente') !== '1') return;
            alvo.removeAttribute('data-cadastro-enter-pendente');
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
        var ativo = global.document.activeElement;
        if (ativo && ativo !== global.document.body && form.contains(ativo) && editavel(ativo)) {
            manterVisivel(ativo);
            return;
        }
        try {
            alvo.focus({ preventScroll: true });
        } catch (_e) {
            alvo.focus();
        }
        if (invalido && alvo.value && alvo.tagName !== 'TEXTAREA' && typeof alvo.select === 'function') {
            alvo.select();
        }
        manterVisivel(alvo);
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
                if (ativo && campoNavegavel(ativo)) manterVisivel(ativo);
            });
        }
    }

    if (global.document.readyState === 'loading') {
        global.document.addEventListener('DOMContentLoaded', iniciar);
    } else {
        iniciar();
    }
}(window));
