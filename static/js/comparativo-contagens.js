/* Comparativo de contagens: atualização leve do resultado.
 * - Polling a cada 5 s somente com a aba visível.
 * - Sem requisições sobrepostas; respostas antigas são descartadas.
 * - Preserva rolagem, foco e detalhes abertos; mantém os dados em falha.
 */
(function () {
    'use strict';

    var INTERVALO_MS = 5000;
    var container = document.getElementById('comparativoResultado');
    if (!container) { return; }

    var botao = document.getElementById('comparativoAtualizar');
    var status = document.getElementById('comparativoStatusAtualizacao');
    var atualizadoEm = document.getElementById('comparativoAtualizadoEm');
    var url = container.getAttribute('data-url');
    var polling = Boolean(url);
    var emAndamento = false;
    var forcarPendente = false;
    var sequencia = 0;
    var ultimaAplicada = 0;
    var abertos = new Set();

    function alternar(toggle) {
        var alvo = document.getElementById(toggle.getAttribute('data-alvo'));
        if (!alvo) { return; }
        var abrir = alvo.hidden;
        alvo.hidden = !abrir;
        toggle.setAttribute('aria-expanded', abrir ? 'true' : 'false');
        if (abrir) { abertos.add(alvo.id); } else { abertos.delete(alvo.id); }
    }

    container.addEventListener('click', function (evento) {
        var toggle = evento.target.closest('.cmp-toggle');
        if (toggle) { alternar(toggle); }
    });

    function restaurarAbertos() {
        abertos.forEach(function (id) {
            var alvo = document.getElementById(id);
            if (!alvo) { abertos.delete(id); return; }
            alvo.hidden = false;
            var toggle = container.querySelector('[data-alvo="' + id + '"]');
            if (toggle) { toggle.setAttribute('aria-expanded', 'true'); }
        });
    }

    function marcarFalha(texto) {
        if (!status) { return; }
        var aviso = status.querySelector('.cmp-falha');
        if (!aviso) {
            aviso = document.createElement('span');
            aviso.className = 'cmp-falha';
            status.appendChild(aviso);
        }
        aviso.textContent = texto ? ' · ' + texto : '';
    }

    function aplicar(html) {
        var wrapAntigo = document.getElementById('comparativoTabelaWrap');
        var rolagemX = wrapAntigo ? wrapAntigo.scrollLeft : 0;
        var rolagemY = window.scrollY;
        var ativo = document.activeElement;
        var focoAlvo = ativo && container.contains(ativo) ? ativo.getAttribute('data-alvo') : null;

        var temp = document.createElement('div');
        temp.innerHTML = html;
        var resumoNovo = temp.querySelector('.comparativo-resumo');
        var carimbo = resumoNovo ? resumoNovo.getAttribute('data-atualizado-em') : '';
        if (resumoNovo) { resumoNovo.removeAttribute('data-atualizado-em'); }
        var resumoAtual = container.querySelector('.comparativo-resumo');
        if (resumoAtual) { resumoAtual.removeAttribute('data-atualizado-em'); }
        restaurarEstadoTemporario(temp);

        // Só troca o DOM quando o conteúdo mudou: evita tremor.
        if (temp.innerHTML !== container.innerHTML) {
            container.innerHTML = temp.innerHTML;
            restaurarAbertos();
            var wrapNovo = document.getElementById('comparativoTabelaWrap');
            if (wrapNovo) { wrapNovo.scrollLeft = rolagemX; }
            window.scrollTo(window.scrollX, rolagemY);
            if (focoAlvo) {
                var foco = container.querySelector('[data-alvo="' + focoAlvo + '"]');
                if (foco) { foco.focus({ preventScroll: true }); }
            }
        }
        if (atualizadoEm && carimbo) { atualizadoEm.textContent = carimbo; }
        marcarFalha('');
    }

    // Aplica os detalhes abertos no HTML recebido antes da comparação, para
    // que um painel aberto não seja tratado como mudança de conteúdo.
    function restaurarEstadoTemporario(temp) {
        abertos.forEach(function (id) {
            var alvo = temp.querySelector('#' + CSS.escape(id));
            if (alvo) { alvo.hidden = false; }
            var toggle = temp.querySelector('[data-alvo="' + id + '"]');
            if (toggle) { toggle.setAttribute('aria-expanded', 'true'); }
        });
    }

    function versaoExibida() {
        var resumo = container.querySelector('.comparativo-resumo');
        return resumo ? (resumo.getAttribute('data-versao') || '') : '';
    }

    function horaAgora() {
        return new Date().toLocaleTimeString('pt-BR');
    }

    // forcar=true (botão Atualizar): servidor relê SAP e contagens e recalcula
    // mesmo sem mudança de versão. Sem forçar, responde 204 se nada mudou.
    function atualizar(forcar) {
        if (!polling) { return; }
        if (emAndamento) {
            if (forcar) { forcarPendente = true; }
            return;
        }
        emAndamento = true;
        var minha = ++sequencia;
        var endereco = url + '&versao=' + encodeURIComponent(versaoExibida()) + (forcar ? '&forcar=1' : '');
        container.classList.add('comparativo-atualizando');
        fetch(endereco, {
            credentials: 'same-origin',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            cache: 'no-store'
        }).then(function (resposta) {
            if (resposta.redirected || !resposta.ok) {
                throw new Error('HTTP ' + resposta.status);
            }
            if (resposta.status === 204) { return null; }
            return resposta.text();
        }).then(function (html) {
            if (minha < ultimaAplicada) { return; }
            ultimaAplicada = minha;
            if (html === null) {
                marcarFalha('');
                marcarVerificado('sem alterações · verificado às ' + horaAgora());
                return;
            }
            aplicar(html);
            marcarVerificado(forcar ? 'reconciliado às ' + horaAgora() : '');
        }).catch(function () {
            marcarFalha('Falha ao atualizar; exibindo os últimos dados recebidos.');
        }).finally(function () {
            emAndamento = false;
            container.classList.remove('comparativo-atualizando');
            if (forcarPendente) {
                forcarPendente = false;
                atualizar(true);
            }
        });
    }

    function marcarVerificado(texto) {
        if (!status) { return; }
        var nota = status.querySelector('.cmp-verificado');
        if (!nota) {
            nota = document.createElement('span');
            nota.className = 'cmp-verificado';
            status.appendChild(nota);
        }
        nota.textContent = texto ? ' · ' + texto : '';
    }

    setInterval(function () {
        if (document.visibilityState === 'visible') { atualizar(false); }
    }, INTERVALO_MS);
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible') { atualizar(false); }
    });
    if (botao) { botao.addEventListener('click', function () { atualizar(true); }); }
}());
