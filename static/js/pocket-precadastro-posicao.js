/**
 * Pocket — cadastro contínuo de posição (fluxo RF: código → posição → salvar).
 * Enter no código valida na hora e avança. Enter na posição só foca Salvar.
 */
(function (global) {
    'use strict';

    var TOAST_MS = 2200;
    var toastTimer = null;
    var ultimoCodigoValidado = '';
    var ultimaValidacaoEhNovo = false;
    var validacaoController = null;
    var validacaoEmAndamento = false;

    function teclaEnter(evento) {
        return evento.key === 'Enter' || evento.key === 'NumpadEnter' ||
            evento.keyCode === 13 || evento.which === 13;
    }

    function focarCampo(el) {
        if (!el || el.disabled || el.readOnly) return;
        // Síncrono: adiar o foco para o frame seguinte sai do gesto e o Android descarta.
        try {
            el.focus({ preventScroll: true });
        } catch (_e) {
            el.focus();
        }
        if (!el.value || typeof el.setSelectionRange !== 'function') return;
        try {
            el.setSelectionRange(0, el.value.length);
        } catch (_e2) { /* campo sem seleção */ }
    }

    function campoTemErro(field) {
        if (!field) return false;
        var grupo = field.closest('.mb-3');
        return !!(grupo && grupo.querySelector('.text-danger'));
    }

    function mostrarToast(mensagem, tipo) {
        var el = global.document.getElementById('pocket-toast');
        if (!el) return;
        if (toastTimer) global.clearTimeout(toastTimer);
        var prefix = tipo === 'ok' ? '\u2713 ' : (tipo === 'erro' ? '\u2716 ' : '');
        el.hidden = false;
        el.textContent = prefix + mensagem;
        el.className = 'pocket-toast pocket-toast--' + (tipo || 'ok');
        toastTimer = global.setTimeout(function () {
            el.hidden = true;
            el.textContent = '';
        }, TOAST_MS);
    }

    function limparAlertasPagina() {
        global.document.querySelectorAll('.page-content .alert').forEach(function (alerta) {
            alerta.remove();
        });
    }

    function setPosicaoHabilitada(posicao, habilitada, limpar) {
        posicao.disabled = !habilitada;
        if (!habilitada && limpar !== false) {
            posicao.value = '';
        }
    }

    function codigoNormalizado(codigo) {
        return (codigo || '').trim().toUpperCase();
    }

    function validarCodigoAjax(url, codigo) {
        if (!codigo) {
            return Promise.resolve({ existe: false });
        }
        if (!url) {
            return Promise.reject(new Error('Endpoint de validação não configurado.'));
        }
        if (validacaoController) {
            validacaoController.abort();
        }
        validacaoController = new AbortController();
        var requestUrl = url + '?codigo=' + encodeURIComponent(codigo);
        return fetch(requestUrl, {
            method: 'GET',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
            },
            signal: validacaoController.signal,
        }).then(function (response) {
            if (!response.ok) {
                throw new Error('Falha ao validar código.');
            }
            return response.json();
        });
    }

    function bloquearFluxoCodigoExistente(codigo, posicao) {
        ultimoCodigoValidado = '';
        ultimaValidacaoEhNovo = false;
        setPosicaoHabilitada(posicao, false);
        codigo.value = '';
        mostrarToast('POSIÇÃO JÁ CADASTRADA', 'erro');
        focarCampo(codigo);
    }

    function liberarFluxoCodigoNovo(codigo, posicao, avancar) {
        ultimoCodigoValidado = codigoNormalizado(codigo.value);
        ultimaValidacaoEhNovo = true;
        setPosicaoHabilitada(posicao, true, false);
        if (avancar) focarCampo(posicao);
    }

    function iniciar() {
        var form = global.document.getElementById('pocket-precadastro-posicao-form');
        if (!form) return;

        var codigo = global.document.getElementById('id_codigo');
        var posicao = global.document.getElementById('id_posicao');
        if (!codigo || !posicao) return;
        var validarCodigoUrl = form.dataset.validarCodigoUrl || '';
        var sucesso = form.dataset.sucesso === '1';

        if (sucesso) {
            codigo.value = '';
            posicao.value = '';
            limparAlertasPagina();
            mostrarToast(form.dataset.mensagem || 'Posição salva.', 'ok');
        }

        var codigoComErro = campoTemErro(codigo);
        var posicaoComErro = campoTemErro(posicao);
        var erroGeral = !!form.querySelector('.alert-danger');
        if (codigoComErro || posicaoComErro || erroGeral) {
            if (posicao.value || posicaoComErro) {
                setPosicaoHabilitada(posicao, true, false);
            } else {
                setPosicaoHabilitada(posicao, false, false);
            }
            if (codigoNormalizado(codigo.value)) {
                ultimoCodigoValidado = codigoNormalizado(codigo.value);
                ultimaValidacaoEhNovo = true;
            }
            if (codigoComErro) focarCampo(codigo);
            else if (posicaoComErro) focarCampo(posicao);
            else focarCampo(codigo);
        } else {
            setPosicaoHabilitada(posicao, false, false);
            focarCampo(codigo);
        }

        function resetarValidacaoSeCodigoMudou() {
            var atual = codigoNormalizado(codigo.value);
            if (atual !== ultimoCodigoValidado) {
                ultimaValidacaoEhNovo = false;
                setPosicaoHabilitada(posicao, false);
            }
        }

        function validarCodigoImediatamente(avancar) {
            if (validacaoEmAndamento) {
                return;
            }

            var valorCodigo = codigoNormalizado(codigo.value);
            if (!valorCodigo) {
                ultimaValidacaoEhNovo = false;
                setPosicaoHabilitada(posicao, false);
                if (avancar) focarCampo(codigo);
                return;
            }

            if (ultimaValidacaoEhNovo && valorCodigo === ultimoCodigoValidado) {
                liberarFluxoCodigoNovo(codigo, posicao, avancar);
                return;
            }

            validacaoEmAndamento = true;
            validarCodigoAjax(validarCodigoUrl, valorCodigo)
                .then(function (data) {
                    if (codigoNormalizado(codigo.value) !== valorCodigo) {
                        return;
                    }
                    if (data && data.existe) {
                        bloquearFluxoCodigoExistente(codigo, posicao);
                        return;
                    }
                    liberarFluxoCodigoNovo(codigo, posicao, avancar);
                })
                .catch(function (error) {
                    if (error && error.name === 'AbortError') {
                        return;
                    }
                    ultimaValidacaoEhNovo = false;
                    setPosicaoHabilitada(posicao, false);
                    mostrarToast('Não foi possível validar o código agora.', 'erro');
                    focarCampo(codigo);
                })
                .finally(function () {
                    validacaoEmAndamento = false;
                });
        }

        codigo.addEventListener('input', function () {
            resetarValidacaoSeCodigoMudou();
        });

        codigo.addEventListener('keydown', function (evento) {
            var enter = teclaEnter(evento);
            var tab = evento.key === 'Tab';
            if (!enter && !tab) return;
            evento.preventDefault();
            evento.stopPropagation();
            codigo.setAttribute('data-codigo-confirma-pendente', '1');
        });

        codigo.addEventListener('keyup', function (evento) {
            var enter = teclaEnter(evento);
            var tab = evento.key === 'Tab';
            if (!enter && !tab) return;
            if (codigo.getAttribute('data-codigo-confirma-pendente') !== '1') return;
            codigo.removeAttribute('data-codigo-confirma-pendente');
            evento.stopPropagation();
            validarCodigoImediatamente(true);
        });

        codigo.addEventListener('blur', function (evento) {
            if (!codigoNormalizado(codigo.value)) {
                return;
            }
            if (ultimaValidacaoEhNovo && codigoNormalizado(codigo.value) === ultimoCodigoValidado) {
                return;
            }
            var destino = evento.relatedTarget;
            var tag = destino && destino.tagName ? destino.tagName.toUpperCase() : '';
            var avancar = tag !== 'BUTTON' && tag !== 'A';
            validarCodigoImediatamente(avancar);
        });

        posicao.addEventListener('keydown', function (evento) {
            if (!teclaEnter(evento)) return;
            evento.preventDefault();
            evento.stopPropagation();
            posicao.setAttribute('data-posicao-enter-pendente', '1');
        });

        posicao.addEventListener('keyup', function (evento) {
            if (!teclaEnter(evento)) return;
            if (posicao.getAttribute('data-posicao-enter-pendente') !== '1') return;
            posicao.removeAttribute('data-posicao-enter-pendente');
            evento.stopPropagation();
            if (!ultimaValidacaoEhNovo) {
                focarCampo(codigo);
                return;
            }
            var salvar = form.querySelector('button[type="submit"]');
            if (salvar) salvar.focus();
        });

        form.addEventListener('submit', function (evento) {
            if (!ultimaValidacaoEhNovo) {
                evento.preventDefault();
                focarCampo(codigo);
            }
        });
    }

    if (global.document.readyState === 'loading') {
        global.document.addEventListener('DOMContentLoaded', iniciar);
    } else {
        iniciar();
    }
}(window));
