/**
 * Pocket — cadastro contínuo de posição (fluxo RF: código → posição → salvar).
 */
(function (global) {
    'use strict';

    var TOAST_MS = 2200;
    var toastTimer = null;
    var ultimoCodigoValidado = '';
    var ultimaValidacaoEhNovo = false;
    var validacaoController = null;

    function focarCampo(el) {
        if (!el || el.disabled || el.readOnly) return;
        global.requestAnimationFrame(function () {
            el.focus();
            if (el.select) el.select();
        });
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

    function setPosicaoHabilitada(posicao, habilitada) {
        posicao.disabled = !habilitada;
        if (!habilitada) {
            posicao.value = '';
        }
    }

    function codigoNormalizado(codigo) {
        return (codigo || '').trim().toUpperCase();
    }

    function validarCodigoAjax(url, codigo) {
        if (!url || !codigo) {
            return Promise.resolve({ existe: false });
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
        mostrarToast('Código já cadastrado.', 'erro');
        focarCampo(codigo);
    }

    function liberarFluxoCodigoNovo(codigo, posicao) {
        ultimoCodigoValidado = codigoNormalizado(codigo.value);
        ultimaValidacaoEhNovo = true;
        setPosicaoHabilitada(posicao, true);
        focarCampo(posicao);
    }

    function iniciar() {
        var form = global.document.getElementById('pocket-precadastro-posicao-form');
        if (!form) return;

        var codigo = global.document.getElementById('id_codigo');
        var posicao = global.document.getElementById('id_posicao');
        if (!codigo || !posicao) return;
        var validarCodigoUrl = form.dataset.validarCodigoUrl || '';

        if (form.dataset.sucesso === '1') {
            codigo.value = '';
            posicao.value = '';
            limparAlertasPagina();
            mostrarToast(form.dataset.mensagem || 'Posição salva.', 'ok');
        }

        setPosicaoHabilitada(posicao, false);

        if (campoTemErro(codigo)) {
            focarCampo(codigo);
        } else if (campoTemErro(posicao)) {
            setPosicaoHabilitada(posicao, true);
            focarCampo(posicao);
        } else {
            focarCampo(codigo);
        }

        function resetarValidacaoSeCodigoMudou() {
            var atual = codigoNormalizado(codigo.value);
            if (atual !== ultimoCodigoValidado) {
                ultimaValidacaoEhNovo = false;
                setPosicaoHabilitada(posicao, false);
            }
        }

        codigo.addEventListener('input', function () {
            resetarValidacaoSeCodigoMudou();
        });

        codigo.addEventListener('keydown', function (evento) {
            if (evento.key !== 'Enter') return;
            evento.preventDefault();
            var valorCodigo = codigoNormalizado(codigo.value);
            if (!valorCodigo) {
                return;
            }

            validarCodigoAjax(validarCodigoUrl, valorCodigo)
                .then(function (data) {
                    if (codigoNormalizado(codigo.value) !== valorCodigo) {
                        return;
                    }
                    if (data && data.existe) {
                        bloquearFluxoCodigoExistente(codigo, posicao);
                        return;
                    }
                    liberarFluxoCodigoNovo(codigo, posicao);
                })
                .catch(function (error) {
                    if (error && error.name === 'AbortError') {
                        return;
                    }
                    mostrarToast('Não foi possível validar o código agora.', 'erro');
                    focarCampo(codigo);
                });
        });

        posicao.addEventListener('keydown', function (evento) {
            if (evento.key !== 'Enter') return;
            evento.preventDefault();
            if (!ultimaValidacaoEhNovo) {
                focarCampo(codigo);
                return;
            }
            if (posicao.value.trim()) {
                form.requestSubmit();
            }
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
