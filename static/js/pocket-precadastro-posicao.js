/**
 * Pocket — cadastro contínuo de posição (fluxo RF: código → posição → salvar).
 */
(function (global) {
    'use strict';

    var TOAST_MS = 2200;
    var toastTimer = null;

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

    function iniciar() {
        var form = global.document.getElementById('pocket-precadastro-posicao-form');
        if (!form) return;

        var codigo = global.document.getElementById('id_codigo');
        var posicao = global.document.getElementById('id_posicao');
        if (!codigo || !posicao) return;

        if (form.dataset.sucesso === '1') {
            codigo.value = '';
            posicao.value = '';
            limparAlertasPagina();
            mostrarToast(form.dataset.mensagem || 'Posição salva.', 'ok');
        }

        if (campoTemErro(codigo)) {
            focarCampo(codigo);
        } else if (campoTemErro(posicao)) {
            focarCampo(posicao);
        } else {
            focarCampo(codigo);
        }

        codigo.addEventListener('keydown', function (evento) {
            if (evento.key !== 'Enter') return;
            evento.preventDefault();
            if (codigo.value.trim()) {
                focarCampo(posicao);
            }
        });

        posicao.addEventListener('keydown', function (evento) {
            if (evento.key !== 'Enter') return;
            evento.preventDefault();
            if (posicao.value.trim()) {
                form.requestSubmit();
            }
        });
    }

    if (global.document.readyState === 'loading') {
        global.document.addEventListener('DOMContentLoaded', iniciar);
    } else {
        iniciar();
    }
}(window));
