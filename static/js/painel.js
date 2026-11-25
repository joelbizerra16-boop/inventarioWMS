/* Painel geral do inventário: troca de conferência sem botão extra. */
(function () {
    'use strict';
    var seletor = document.querySelector('select[data-pn-enviar]');
    if (seletor && seletor.form) {
        seletor.addEventListener('change', function () { seletor.form.submit(); });
    }
}());
