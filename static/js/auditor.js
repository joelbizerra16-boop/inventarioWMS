/* Painel do Auditor: abas, tela cheia e atualização automática leve.
 * - Atualiza somente com a página visível, sem requisições sobrepostas.
 * - Troca apenas as regiões de indicadores e tabela (filtros não são tocados).
 * - Respostas antigas são descartadas; 204 = nada mudou (sem redesenho).
 * - Falha de atualização: avisa e marca os dados exibidos como desatualizados.
 * - 403: acesso suspenso — os dados saem da tela imediatamente.
 */
(function () {
    'use strict';

    var seletor = document.querySelector('select[data-aud-navegar]');
    if (seletor) {
        seletor.addEventListener('change', function () { window.location.href = seletor.value; });
    }

    // ------------------------------------------------------------ tela cheia
    var botaoTela = document.getElementById('audTelaCheia');
    if (botaoTela) {
        var raiz = document.documentElement;
        var suportado = !!(document.fullscreenEnabled || document.webkitFullscreenEnabled) &&
            !!(raiz.requestFullscreen || raiz.webkitRequestFullscreen);
        var emTelaCheia = function () { return !!(document.fullscreenElement || document.webkitFullscreenElement); };
        var sincronizarBotao = function () {
            var ativo = emTelaCheia();
            botaoTela.setAttribute('aria-pressed', ativo ? 'true' : 'false');
            botaoTela.querySelector('span').textContent = ativo ? 'Sair da tela cheia' : 'Tela cheia';
            botaoTela.querySelector('i').className = ativo ? 'bi bi-fullscreen-exit' : 'bi bi-arrows-fullscreen';
            botaoTela.title = ativo ? 'Sair da tela cheia (Esc)' : 'Entrar em tela cheia';
            document.body.classList.toggle('aud-tela-cheia', ativo);
        };
        if (!suportado) {
            botaoTela.disabled = true;
            botaoTela.title = 'Tela cheia não é suportada por este navegador.';
        } else {
            botaoTela.addEventListener('click', function () {
                try {
                    if (emTelaCheia()) {
                        (document.exitFullscreen || document.webkitExitFullscreen).call(document);
                    } else {
                        var pedido = (raiz.requestFullscreen || raiz.webkitRequestFullscreen).call(raiz);
                        if (pedido && pedido.catch) { pedido.catch(function () { sincronizarBotao(); }); }
                    }
                } catch (e) { sincronizarBotao(); }
            });
            // Esc e outras saídas: o estado do botão segue o estado real.
            document.addEventListener('fullscreenchange', sincronizarBotao);
            document.addEventListener('webkitfullscreenchange', sincronizarBotao);
        }
    }

    // ------------------------------------------------------------ painel
    var painel = document.getElementById('audPainel');
    if (!painel) { return; }
    var urlBase = painel.getAttribute('data-url');
    var intervalo = Math.max(5, parseInt(painel.getAttribute('data-intervalo'), 10) || 8) * 1000;
    var hora = document.getElementById('audSyncHora');
    var textoSync = document.getElementById('audSyncTexto');
    var sync = document.getElementById('audSync');
    var campoAba = document.querySelector('[data-aud-aba-campo]');
    var emAndamento = false;
    var encerrado = false;
    var sequencia = 0;
    var aplicada = 0;
    var ultimaHora = hora ? hora.textContent : '';
    var abaInicial = painel.querySelector('.aud-aba.ativa');
    var abaAtual = abaInicial ? abaInicial.getAttribute('data-aud-aba') : 'posicao';
    var campoGe = document.querySelector('[data-aud-ge-campo]');
    var geInicial = painel.querySelector('[data-aud-ge].ativa');
    var geAtual = geInicial ? geInicial.getAttribute('data-aud-ge') : '';
    var modoAtual = 'acumulado';

    function definirParametro(nome, valor) {
        try {
            var url = new URL(window.location.href);
            url.searchParams.set(nome, valor);
            window.history.replaceState(null, '', url.toString());
        } catch (e) { /* navegador antigo: segue sem atualizar a URL */ }
    }

    function mostrarGrafico(etapa) {
        if (!etapa) { return; }
        geAtual = etapa;
        painel.querySelectorAll('[data-aud-ge]').forEach(function (b) {
            var ativa = b.getAttribute('data-aud-ge') === etapa;
            b.classList.toggle('ativa', ativa);
            b.setAttribute('aria-pressed', ativa ? 'true' : 'false');
        });
        painel.querySelectorAll('[data-aud-grafico-etapa]').forEach(function (sec) {
            sec.classList.toggle('d-none', sec.getAttribute('data-aud-grafico-etapa') !== etapa);
        });
        if (campoGe) { campoGe.value = etapa; }
        definirParametro('ge', etapa);
    }

    function mostrarModo(modo) {
        modoAtual = modo;
        painel.querySelectorAll('[data-aud-modo]').forEach(function (b) {
            var ativo = b.getAttribute('data-aud-modo') === modo;
            b.classList.toggle('ativa', ativo);
            b.setAttribute('aria-pressed', ativo ? 'true' : 'false');
        });
        painel.querySelectorAll('[data-aud-serie]').forEach(function (svg) {
            svg.classList.toggle('d-none', svg.getAttribute('data-aud-serie') !== modo);
        });
        painel.querySelectorAll('[data-aud-legenda-modo]').forEach(function (el) {
            el.classList.toggle('d-none', el.getAttribute('data-aud-legenda-modo') !== modo);
        });
    }

    // Filtro de status: aplica ao escolher (a busca aplica com Enter).
    var autoenvio = document.querySelector('[data-aud-autoenvio]');
    if (autoenvio && autoenvio.form) {
        autoenvio.addEventListener('change', function () { autoenvio.form.submit(); });
    }

    // ---------------------------------------------------------- detalhes
    var modal = document.getElementById('audDetalhe');
    var CAMPOS_DETALHE = [
        ['posicao', 'Posição'], ['sku', 'SKU'], ['descricao', 'Descrição'], ['contagem', 'Contagem'],
        ['quantidade', 'Quantidade contada'], ['situacao', 'Situação'], ['operador', 'Operador responsável'],
        ['registrado', 'Data e hora reais da coleta'], ['liberado', 'Liberação da contagem'],
        ['disponibilizado', 'Disponibilizado ao auditor'], ['corrigido', 'Correção posterior']
    ];
    function abrirDetalhe(botao) {
        if (!modal) { return; }
        var corpo = document.getElementById('audDetalheCorpo');
        corpo.innerHTML = '';
        CAMPOS_DETALHE.forEach(function (campo) {
            var valor = botao.getAttribute('data-' + campo[0]);
            if (!valor) { return; }
            var dt = document.createElement('dt');
            dt.textContent = campo[1];
            var dd = document.createElement('dd');
            dd.textContent = valor;
            corpo.appendChild(dt);
            corpo.appendChild(dd);
        });
        document.getElementById('audDetalheTitulo').textContent = botao.getAttribute('data-titulo') || 'Detalhes da contagem';
        if (window.bootstrap && window.bootstrap.Modal) {
            window.bootstrap.Modal.getOrCreateInstance(modal).show();
        }
    }

    // ---------------------------------------------------------- exportação
    var botaoExportar = document.getElementById('audExportar');
    var statusExportar = document.getElementById('audExportarStatus');
    if (botaoExportar) {
        botaoExportar.addEventListener('click', function () {
            var url = new URL(botaoExportar.getAttribute('data-url'), window.location.href);
            new URL(window.location.href).searchParams.forEach(function (valor, chave) {
                if (['pp', 'ps', 'ph', 'versao', 'forcar'].indexOf(chave) === -1) { url.searchParams.set(chave, valor); }
            });
            url.searchParams.set('aba', abaAtual === 'sku' ? 'sku' : 'posicao');
            botaoExportar.disabled = true;
            statusExportar.className = 'aud-exportar-status';
            statusExportar.textContent = 'Gerando arquivo…';
            fetch(url.toString(), { credentials: 'same-origin', cache: 'no-store' })
                .then(function (resposta) {
                    if (resposta.status === 403) { encerrar(); throw new Error('403'); }
                    if (!resposta.ok || resposta.redirected) { throw new Error(String(resposta.status)); }
                    var disposicao = resposta.headers.get('Content-Disposition') || '';
                    var nome = (disposicao.match(/filename="([^"]+)"/) || [])[1] || 'auditoria.xlsx';
                    return resposta.blob().then(function (blob) { return [blob, nome]; });
                })
                .then(function (par) {
                    var link = document.createElement('a');
                    link.href = URL.createObjectURL(par[0]);
                    link.download = par[1];
                    document.body.appendChild(link);
                    link.click();
                    link.remove();
                    setTimeout(function () { URL.revokeObjectURL(link.href); }, 2000);
                    statusExportar.className = 'aud-exportar-status aud-ok';
                    statusExportar.textContent = 'Arquivo exportado às ' + new Date().toLocaleTimeString('pt-BR');
                })
                .catch(function () {
                    statusExportar.className = 'aud-exportar-status aud-erro';
                    statusExportar.textContent = 'Falha ao exportar. Tente novamente.';
                })
                .finally(function () { botaoExportar.disabled = false; });
        });
    }

    function linhasConhecidas() {
        var chaves = new Set();
        painel.querySelectorAll('[data-aud-linha]').forEach(function (el) { chaves.add(el.getAttribute('data-aud-linha')); });
        return chaves;
    }
    var conhecidas = linhasConhecidas();

    function mostrarAba(aba) {
        abaAtual = aba;
        painel.querySelectorAll('.aud-aba').forEach(function (b) {
            var ativa = b.getAttribute('data-aud-aba') === aba;
            b.classList.toggle('ativa', ativa);
            b.setAttribute('aria-selected', ativa ? 'true' : 'false');
        });
        painel.querySelectorAll('[data-aud-painel]').forEach(function (p) {
            p.classList.toggle('d-none', p.getAttribute('data-aud-painel') !== aba);
        });
        if (campoAba) { campoAba.value = aba; }
        try {
            var url = new URL(window.location.href);
            url.searchParams.set('aba', aba);
            window.history.replaceState(null, '', url.toString());
        } catch (e) { /* navegador antigo: segue sem atualizar a URL */ }
    }

    painel.addEventListener('click', function (ev) {
        var aba = ev.target.closest('[data-aud-aba]');
        if (aba) { mostrarAba(aba.getAttribute('data-aud-aba')); return; }
        var ge = ev.target.closest('[data-aud-ge]');
        if (ge) { mostrarGrafico(ge.getAttribute('data-aud-ge')); return; }
        var modo = ev.target.closest('[data-aud-modo]');
        if (modo) { mostrarModo(modo.getAttribute('data-aud-modo')); return; }
        if (ev.target.closest('[data-aud-alternar-modo]')) {
            mostrarModo(modoAtual === 'acumulado' ? 'intervalo' : 'acumulado');
            return;
        }
        var detalhe = ev.target.closest('[data-aud-detalhe]');
        if (detalhe) { abrirDetalhe(detalhe); }
    });

    function versaoAtual() {
        var el = painel.querySelector('[data-aud-regiao="indicadores"]');
        return el ? el.getAttribute('data-aud-versao') || '' : '';
    }

    function urlAtualizacao(forcar) {
        var url = new URL(urlBase, window.location.href);
        url.searchParams.set('aba', abaAtual);
        if (geAtual) { url.searchParams.set('ge', geAtual); }
        url.searchParams.set('versao', versaoAtual());
        if (forcar) { url.searchParams.set('forcar', '1'); }
        return url.toString();
    }

    function aplicar(html) {
        var modelo = document.createElement('div');
        modelo.innerHTML = html;
        var rolagens = [];
        painel.querySelectorAll('.aud-rolagem').forEach(function (el, i) { rolagens[i] = [el.scrollLeft, el.scrollTop]; });
        var y = window.scrollY;
        var anteriores = conhecidas;
        modelo.querySelectorAll('[data-aud-regiao]').forEach(function (nova) {
            var atual = painel.querySelector('[data-aud-regiao="' + nova.getAttribute('data-aud-regiao') + '"]');
            if (atual) { atual.replaceWith(nova); }
        });
        mostrarAba(abaAtual);
        mostrarGrafico(geAtual);
        mostrarModo(modoAtual);
        painel.querySelectorAll('.aud-rolagem').forEach(function (el, i) {
            if (rolagens[i]) { el.scrollLeft = rolagens[i][0]; el.scrollTop = rolagens[i][1]; }
        });
        window.scrollTo(window.scrollX, y);
        conhecidas = linhasConhecidas();
        conhecidas.forEach(function (chave) {
            if (!anteriores.has(chave)) {
                var el = painel.querySelector('[data-aud-linha="' + chave + '"]');
                if (el) { el.classList.add('aud-novo'); }
            }
        });
    }

    function marcarSucesso() {
        ultimaHora = new Date().toLocaleTimeString('pt-BR');
        if (textoSync) { textoSync.innerHTML = 'Atualizado às <span id="audSyncHora"></span>'; }
        var alvo = document.getElementById('audSyncHora');
        if (alvo) { alvo.textContent = ultimaHora; }
        if (sync) { sync.classList.remove('aud-sync-falha'); }
        painel.classList.remove('aud-desatualizado');
    }

    function marcarFalha() {
        var agora = new Date().toLocaleTimeString('pt-BR');
        if (textoSync) {
            textoSync.textContent = 'Falha ao atualizar às ' + agora + ' · exibindo dados de ' + ultimaHora;
        }
        if (sync) { sync.classList.add('aud-sync-falha'); }
        painel.classList.add('aud-desatualizado');
    }

    function encerrar() {
        encerrado = true;
        painel.querySelectorAll('[data-aud-regiao]').forEach(function (el) {
            el.innerHTML = '<p class="aud-semdados" role="alert">O acesso a esta conferência foi suspenso ou não está mais liberado. Os dados deixaram de ser exibidos.</p>';
        });
        if (textoSync) { textoSync.textContent = 'Acesso suspenso'; }
        if (sync) { sync.classList.add('aud-sync-falha'); }
    }

    function atualizar(forcar) {
        if (emAndamento || encerrado) { return; }
        emAndamento = true;
        var minha = ++sequencia;
        fetch(urlAtualizacao(forcar), { credentials: 'same-origin', cache: 'no-store', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (resposta) {
                if (resposta.status === 403) { encerrar(); return null; }
                if (resposta.redirected || !resposta.ok) { throw new Error(String(resposta.status)); }
                return resposta.status === 204 ? '' : resposta.text();
            })
            .then(function (html) {
                if (encerrado || html === null || minha < aplicada) { return; }
                aplicada = minha;
                if (html) { aplicar(html); }
                marcarSucesso();
            })
            .catch(function () { marcarFalha(); })
            .finally(function () { emAndamento = false; });
    }

    setInterval(function () { if (document.visibilityState === 'visible') { atualizar(false); } }, intervalo);
    document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible') { atualizar(false); } });
    var botaoAtualizar = document.getElementById('audAtualizar');
    if (botaoAtualizar) { botaoAtualizar.addEventListener('click', function () { atualizar(true); }); }
}());
