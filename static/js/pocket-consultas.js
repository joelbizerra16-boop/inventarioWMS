/**
 * Consulta Pocket — uma pesquisa por envio, sem abrir teclado virtual.
 */
(function () {
    'use strict';

    var form = document.getElementById('pocket-consultas-form');
    if (!form) {
        return;
    }

    var campo = document.getElementById('pocket-consultas-q');
    var enviando = false;

    form.addEventListener('submit', function (event) {
        var valor = campo ? String(campo.value || '').replace(/^\s+|\s+$/g, '') : '';
        if (!valor) {
            event.preventDefault();
            return;
        }
        if (enviando) {
            event.preventDefault();
            return;
        }
        enviando = true;
        if (campo) {
            campo.value = valor;
        }
    });
}());
