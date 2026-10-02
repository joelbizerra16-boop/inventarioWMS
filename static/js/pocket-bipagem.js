/**
 * Pocket Bipagem — coletor WMS (foco em bipagem contínua).
 */
(function (global) {
    'use strict';

    var audioCtx = null;
    var toastTimer = null;
    var TOAST_MS = 2600;
    // Único atraso da validação de Posição e Produto/EAN. Não vale para Quantidade.
    var POCKET_VALIDACAO_ATRASO_MS = 2000;

    function opcoesFetchPocket(csrfToken) {
        return {
            credentials: 'same-origin',
            redirect: 'manual',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': csrfToken || '',
            },
        };
    }

    function parsearRespostaPocket(res) {
        console.info('[PocketBipagem] resposta HTTP', {
            status: res.status,
            redirected: res.redirected,
            url: res.url,
            type: res.type,
            contentType: res.headers.get('content-type'),
        });
        var contentType = (res.headers.get('content-type') || '').toLowerCase();
        if (res.type === 'opaqueredirect' || res.status === 0) {
            return Promise.reject(new Error(
                'Redirect opaco após POST (status=' + res.status + ' url=' + res.url + ')'
            ));
        }
        if (res.redirected) {
            return Promise.reject(new Error(
                'Redirect inesperado após POST (status=' + res.status + ' url=' + res.url + ')'
            ));
        }
        if (res.status === 301 || res.status === 302 || res.status === 303 ||
            res.status === 307 || res.status === 308) {
            var location = res.headers.get('location') || res.url || '';
            return Promise.reject(new Error(
                'Servidor retornou redirect HTTP ' + res.status + ' para ' + location
            ));
        }
        if (!contentType.includes('application/json')) {
            return res.text().then(function (texto) {
                var amostra = (texto || '').trim().slice(0, 160);
                throw new Error(
                    'Resposta inválida (status=' + res.status +
                    ' content-type=' + (contentType || 'vazio') +
                    ' corpo=' + amostra + ')'
                );
            });
        }
        return res.json().then(function (body) {
            return { ok: res.ok, status: res.status, body: body };
        });
    }

    function tratarErroFetchPocket(erro, toastFn) {
        console.error('[PocketBipagem] falha na requisição:', erro);
        var msg = 'Falha na comunicação com o servidor.';
        if (erro && erro.message) {
            if (erro.message.indexOf('Failed to fetch') >= 0 ||
                erro.message.indexOf('NetworkError') >= 0 ||
                erro.message.indexOf('Load failed') >= 0) {
                msg = 'Sem conexão';
            } else {
                msg = erro.message;
            }
        }
        if (toastFn) toastFn(msg, 'erro');
    }

    function processarSucessoPocket(callback, dados, limparFn, toastFn) {
        try {
            if (callback) callback(dados);
            if (limparFn) limparFn();
        } catch (err) {
            console.error('[PocketBipagem] erro ao processar sucesso:', err);
            if (toastFn) {
                toastFn('Operação salva, mas houve erro ao atualizar a tela.', 'alerta');
            }
        }
    }

    function obterAudioContext() {
        if (!audioCtx) {
            var Ctx = global.AudioContext || global.webkitAudioContext;
            if (Ctx) audioCtx = new Ctx();
        }
        return audioCtx;
    }

    function tocarTom(frequencia, duracaoMs, tipo, volume) {
        var ctx = obterAudioContext();
        if (!ctx) return;
        try {
            if (ctx.state === 'suspended') ctx.resume();
            var osc = ctx.createOscillator();
            var gain = ctx.createGain();
            osc.type = tipo || 'sine';
            osc.frequency.value = frequencia;
            gain.gain.value = volume != null ? volume : 0.22;
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start();
            osc.stop(ctx.currentTime + duracaoMs / 1000);
        } catch (_e) { /* sem áudio */ }
    }

    var Sons = {
        ok: function () { tocarTom(880, 70, 'sine', 0.18); },
        erro: function () {
            tocarTom(220, 160, 'square', 0.16);
            global.setTimeout(function () { tocarTom(180, 200, 'square', 0.16); }, 180);
        },
        posicaoInvalida: function () {
            tocarTom(330, 110, 'sawtooth', 0.14);
            global.setTimeout(function () { tocarTom(260, 150, 'sawtooth', 0.14); }, 120);
        },
        produtoInvalido: function () {
            tocarTom(440, 90, 'triangle', 0.16);
            global.setTimeout(function () { tocarTom(350, 130, 'triangle', 0.16); }, 100);
        },
        bloqueado: function () { tocarTom(150, 280, 'square', 0.18); },
    };

    function focarCampo(el, selecionar) {
        if (!el || el.disabled || el.readOnly) return;
        // Um disparo síncrono, no init ou no Enter.
        // Adiar o foco para o frame seguinte sai do gesto e o Android descarta.
        try {
            el.focus({ preventScroll: true });
        } catch (_e) {
            el.focus();
        }
        // Campo vazio não fica selecionado: no Android o select() faz o wedge
        // seguinte substituir cada tecla pela anterior e sobra só o último caractere.
        if (selecionar === false || !el.value) return;
        if (typeof el.select === 'function') {
            try {
                el.select();
            } catch (_e2) { /* campo sem seleção */ }
        }
    }

    function focoNoCampo(input) {
        return !!input && global.document.activeElement === input;
    }

    function selecionarCampo(input) {
        if (!focoNoCampo(input) || typeof input.select !== 'function') return;
        try {
            input.select();
        } catch (_e) { /* campo sem seleção */ }
    }

    function leituraAindaAtual(input, codigo) {
        return !!input && input.value.trim() === codigo;
    }

    function envoltorioCampo(input) {
        if (input.closest) {
            var achado = input.closest('.pocket-scan-input-wrap');
            if (achado) return achado;
        }
        return input.parentNode;
    }

    function obterMensagemCampo(input, criar) {
        if (!input) return null;
        var chave = input.id || input.name || '';
        var wrap = envoltorioCampo(input);
        if (!wrap || !wrap.parentNode) return null;
        var el = wrap.parentNode.querySelector(
            '.pocket-campo-erro[data-for="' + chave + '"]'
        );
        if (!el && criar) {
            el = global.document.createElement('p');
            el.className = 'pocket-campo-erro';
            el.setAttribute('data-for', chave);
            el.setAttribute('role', 'status');
            el.hidden = true;
            wrap.insertAdjacentElement('afterend', el);
        }
        return el;
    }

    function mostrarMensagemCampo(input, texto) {
        var el = obterMensagemCampo(input, true);
        if (!el) return;
        el.textContent = texto;
        el.hidden = false;
    }

    function limparMensagemCampo(input) {
        var el = obterMensagemCampo(input, false);
        if (!el) return;
        el.textContent = '';
        el.hidden = true;
    }

    function sinalizarCodigoInvalido(input, mensagem, som) {
        marcarErro(input, true);
        mostrarMensagemCampo(input, mensagem);
        if (som) som();
        selecionarCampo(input);
    }

    function mensagemEhFalhaRede(msg) {
        if (!msg) return false;
        var m = String(msg).toLowerCase();
        return m.indexOf('sem conexão') >= 0 ||
            m.indexOf('sem conexao') >= 0 ||
            m.indexOf('falha na comunicação') >= 0 ||
            m.indexOf('falha na comunicacao') >= 0 ||
            m.indexOf('failed to fetch') >= 0 ||
            m.indexOf('networkerror') >= 0 ||
            m.indexOf('load failed') >= 0 ||
            m.indexOf('resposta inválida') >= 0 ||
            m.indexOf('resposta invalida') >= 0 ||
            m.indexOf('redirect') >= 0;
    }

    function avisarFalhaRede() {
        Sons.erro();
        toast('Falha na comunicação com o servidor.', 'erro');
    }

    function marcarErro(input, ativo) {
        if (!input) return;
        input.classList.toggle('pocket-input--erro', !!ativo);
    }

    function toast(mensagem, tipo) {
        var el = global.document.getElementById('pocket-toast');
        if (!el) return;
        if (toastTimer) global.clearTimeout(toastTimer);
        var prefix = tipo === 'ok' ? '\u2713 ' : (tipo === 'erro' ? '\u2716 ' : '');
        el.hidden = false;
        el.textContent = prefix + mensagem;
        el.className = 'pocket-toast';
        if (tipo === 'ok') el.classList.add('pocket-toast--ok');
        else if (tipo === 'erro') el.classList.add('pocket-toast--erro');
        else if (tipo === 'alerta') el.classList.add('pocket-toast--alerta');
        toastTimer = global.setTimeout(function () {
            el.hidden = true;
            el.textContent = '';
        }, TOAST_MS);
    }

    function classificarErroServidor(msg) {
        if (!msg) return { titulo: 'Erro', tipo: 'erro' };
        var m = msg.toLowerCase();
        if (m.indexOf('contagem por') >= 0 || m.indexOf('atribuída') >= 0 ||
            m.indexOf('posição em contagem') >= 0 || m.indexOf('posicao em contagem') >= 0 ||
            m.indexOf('bloquead') >= 0 || m.indexOf('outro operador') >= 0) {
            return { titulo: 'Posição em contagem por outro operador', tipo: 'erro' };
        }
        if (m.indexOf('não pertence') >= 0 || m.indexOf('nao pertence') >= 0) {
            return { titulo: 'Produto não pertence à posição', tipo: 'erro' };
        }
        if (m.indexOf('posição não') >= 0 || m.indexOf('posicao não') >= 0 ||
            m.indexOf('posição n') >= 0) {
            return { titulo: 'Posição inválida', tipo: 'erro' };
        }
        if (m.indexOf('produto não') >= 0 || m.indexOf('produto n') >= 0) {
            return { titulo: 'Produto inválido', tipo: 'erro' };
        }
        if (m.indexOf('já inventariado') >= 0 || m.indexOf('ja inventariado') >= 0 ||
            m.indexOf('já foi contado') >= 0 || m.indexOf('ja foi contado') >= 0) {
            return { titulo: 'Produto já inventariado nesta posição', tipo: 'erro' };
        }
        if (m.indexOf('inventário #') >= 0 || m.indexOf('inventario #') >= 0) {
            return { titulo: msg, tipo: 'erro' };
        }
        if (m.indexOf('inventário não') >= 0 || m.indexOf('inventario não') >= 0 ||
            m.indexOf('inventário n') >= 0 || m.indexOf('inventario n') >= 0) {
            return { titulo: msg || 'Inventário não encontrado', tipo: 'erro' };
        }
        if (m.indexOf('registro não') >= 0 || m.indexOf('registro n') >= 0) {
            return { titulo: 'Registro não encontrado', tipo: 'erro' };
        }
        if (m.indexOf('diverg') >= 0) {
            return { titulo: 'Divergência encontrada', tipo: 'alerta' };
        }
        if (m.indexOf('produto divergente') >= 0) {
            return { titulo: 'Produto divergente do SKU selecionado.', tipo: 'erro' };
        }
        return { titulo: msg, tipo: 'erro' };
    }

    function teclaConfirma(evento) {
        return evento.key === 'Enter' || evento.key === 'NumpadEnter' ||
            evento.keyCode === 13 || evento.which === 13;
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

    function registrarEnter(campo, callback) {
        if (!campo) return;
        instalarRecepcaoDeLeitura(campo);
        var pendente = false;
        campo.addEventListener('keydown', function (evento) {
            if (!teclaConfirma(evento)) return;
            evento.preventDefault();
            evento.stopPropagation();
            pendente = true;
        });
        campo.addEventListener('keyup', function (evento) {
            if (!pendente || !teclaConfirma(evento)) return;
            pendente = false;
            callback();
        });
    }

    function criarControleValidacaoLeitura(opcoes) {
        var timer = null;
        var compondo = false;
        var emVoo = '';
        var ultimoInvalido = '';
        var seq = 0;

        function cancelarTimer() {
            if (timer !== null) {
                opcoes.cancelarTimer(timer);
                timer = null;
            }
        }

        function valorLimpo() {
            return opcoes.lerValor();
        }

        function disparar() {
            cancelarTimer();
            if (compondo) return;
            var valor = valorLimpo();
            if (!valor) return;
            if (valor === ultimoInvalido) return;
            if (emVoo === valor) return;
            var minhaSeq = ++seq;
            emVoo = valor;
            opcoes.validar(valor, function (resultado) {
                if (minhaSeq !== seq) return;
                if (valorLimpo() !== valor) {
                    if (emVoo === valor) emVoo = '';
                    return;
                }
                if (emVoo === valor) emVoo = '';
                if (resultado === 'invalido') ultimoInvalido = valor;
            });
        }

        function agendar() {
            cancelarTimer();
            if (compondo) return;
            if (!valorLimpo()) return;
            timer = opcoes.agendar(function () {
                timer = null;
                disparar();
            }, opcoes.atrasoMs);
        }

        return {
            cancelar: cancelarTimer,
            emComposicao: function () { return compondo; },
            aoDigitar: function () {
                seq += 1;
                emVoo = '';
                ultimoInvalido = '';
                if (opcoes.aoAlterar) opcoes.aoAlterar();
                agendar();
            },
            aoEnter: function () {
                if (compondo) return;
                disparar();
            },
            aoCompositionStart: function () {
                compondo = true;
                cancelarTimer();
            },
            aoCompositionEnd: function () {
                compondo = false;
                ultimoInvalido = '';
                agendar();
            },
        };
    }

    function registrarLeituraCampo(campo, opcoes) {
        if (!campo) {
            return { cancelar: function () {} };
        }
        var controle = criarControleValidacaoLeitura({
            atrasoMs: POCKET_VALIDACAO_ATRASO_MS,
            lerValor: function () { return (campo.value || '').trim(); },
            agendar: function (fn, ms) { return global.setTimeout(fn, ms); },
            cancelarTimer: function (id) { global.clearTimeout(id); },
            aoAlterar: opcoes.aoAlterar,
            validar: function (_valor, done) { opcoes.validar(done); },
        });
        campo.addEventListener('input', function () {
            controle.aoDigitar();
        });
        campo.addEventListener('compositionstart', function () {
            controle.aoCompositionStart();
        });
        campo.addEventListener('compositionend', function () {
            controle.aoCompositionEnd();
        });
        instalarRecepcaoDeLeitura(campo);
        var confirmaPendente = false;
        campo.addEventListener('keydown', function (evento) {
            var tab = !!(opcoes.tabConfirma && evento.key === 'Tab');
            var confirma = teclaConfirma(evento) || tab;
            if (!confirma) return;
            evento.preventDefault();
            evento.stopPropagation();
            if (controle.emComposicao()) return;
            controle.cancelar();
            confirmaPendente = true;
        });
        campo.addEventListener('keyup', function (evento) {
            if (!confirmaPendente) return;
            var tab = !!(opcoes.tabConfirma && evento.key === 'Tab');
            if (!teclaConfirma(evento) && !tab) return;
            confirmaPendente = false;
            if (controle.emComposicao()) return;
            controle.aoEnter();
        });
        return { cancelar: controle.cancelar };
    }

    function initAudioTouch() {
        global.document.body.addEventListener('touchstart', function desbloquear() {
            obterAudioContext();
            global.document.body.removeEventListener('touchstart', desbloquear);
        }, { once: true });
    }

    var MESTRES_SYNC_MS = 60000;
    var syncMestresEmAndamento = null;

    function assinaturaMestres(config) {
        return [
            Object.keys(config.mapaPosicoes || {}).sort().join('\u0001'),
            Object.keys(config.mapaProdutos || {}).sort().join('\u0001'),
            Object.keys(config.mapaEan || {}).sort().join('\u0001'),
        ].join('\u0002');
    }

    function substituirMapa(destino, origem) {
        if (!destino || !origem) {
            return;
        }
        Object.keys(destino).forEach(function (chave) {
            delete destino[chave];
        });
        Object.keys(origem).forEach(function (chave) {
            destino[chave] = origem[chave];
        });
    }

    function aplicarDadosMestres(config, dados) {
        if (dados.mapa_posicoes) {
            substituirMapa(config.mapaPosicoes, dados.mapa_posicoes);
        }
        if (dados.mapa_produtos) {
            substituirMapa(config.mapaProdutos, dados.mapa_produtos);
        }
        if (dados.mapa_ean) {
            substituirMapa(config.mapaEan, dados.mapa_ean);
        }
        if (dados.mapa_embalagens && config.mapaEmbalagens) {
            substituirMapa(config.mapaEmbalagens, dados.mapa_embalagens);
        }
    }

    function sincronizarDadosMestres(config) {
        if (!config.mestresUrl) {
            return Promise.resolve({ alterado: false, ok: true });
        }
        if (syncMestresEmAndamento) {
            return syncMestresEmAndamento;
        }
        var assinaturaAnterior = assinaturaMestres(config);
        syncMestresEmAndamento = fetch(config.mestresUrl, {
            method: 'GET',
            credentials: 'same-origin',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
        })
            .then(function (res) {
                return res.json();
            })
            .then(function (body) {
                syncMestresEmAndamento = null;
                if (!body.ok) {
                    return { alterado: false, ok: false };
                }
                aplicarDadosMestres(config, body);
                return {
                    alterado: assinaturaMestres(config) !== assinaturaAnterior,
                    ok: true,
                };
            })
            .catch(function () {
                syncMestresEmAndamento = null;
                return { alterado: false, ok: false };
            });
        return syncMestresEmAndamento;
    }

    function notificarMestresAtualizados(sync) {
        if (sync && sync.alterado) {
            toast('Dados atualizados automaticamente.', 'ok');
        }
    }

    function resolverPosicaoComSync(config, codigo, callback) {
        if (!codigo) {
            callback(false, null, false);
            return;
        }
        if (config.mapaPosicoes[codigo]) {
            callback(true, config.mapaPosicoes[codigo], false);
            return;
        }
        sincronizarDadosMestres(config).then(function (sync) {
            if (!sync.ok) {
                callback(false, null, false, true);
                return;
            }
            var ok = !!config.mapaPosicoes[codigo];
            callback(ok, ok ? config.mapaPosicoes[codigo] : null, sync.alterado && ok, false);
        });
    }

    function iniciarSincronizacaoPeriodica(config) {
        if (!config.mestresUrl) {
            return;
        }
        global.setInterval(function () {
            sincronizarDadosMestres(config).then(function (sync) {
                notificarMestresAtualizados(sync);
            });
        }, MESTRES_SYNC_MS);
    }

    function exibirPosicao(descricao, posicaoConfirm, posicaoLabel) {
        if (posicaoLabel) posicaoLabel.textContent = descricao;
        if (posicaoConfirm) posicaoConfirm.classList.remove('pocket-scan-confirm--hidden');
    }

    function ocultarPosicao(posicaoConfirm, posicaoLabel) {
        if (posicaoLabel) posicaoLabel.textContent = '\u2014';
        if (posicaoConfirm) posicaoConfirm.classList.add('pocket-scan-confirm--hidden');
    }

    function exibirDescricao(texto, descricaoConfirm, produtoDescricao) {
        if (produtoDescricao) produtoDescricao.textContent = texto;
        if (descricaoConfirm) descricaoConfirm.classList.remove('pocket-scan-confirm--hidden');
    }

    function ocultarDescricao(descricaoConfirm, produtoDescricao) {
        if (produtoDescricao) produtoDescricao.textContent = '\u2014';
        if (descricaoConfirm) descricaoConfirm.classList.add('pocket-scan-confirm--hidden');
    }

    function validarQuantidade(input, silencioso) {
        if (!input) return false;
        var raw = input.value.trim();
        var invalida = raw === '' || !/^-?\d+$/.test(raw) || parseInt(raw, 10) <= 0;
        if (invalida) {
            marcarErro(input, true);
            if (!silencioso) {
                Sons.erro();
                toast('Quantidade inválida', 'erro');
            }
            return false;
        }
        marcarErro(input, false);
        return true;
    }

    function resetarQuantidade(input) {
        if (input) input.value = '';
    }

    function focarErroServidor(form, errors) {
        if (!form || !errors) return;
        var ordem = ['codigo_posicao', 'codigo_produto', 'codigo_produto_lido', 'quantidade_fisica'];
        var alvo = '';
        var i;
        for (i = 0; i < ordem.length; i++) {
            if (errors[ordem[i]]) {
                alvo = ordem[i];
                break;
            }
        }
        if (!alvo) {
            var chaves = Object.keys(errors);
            if (chaves.length) alvo = chaves[0];
        }
        if (!alvo) return;
        focarCampo(form.querySelector('[name="' + alvo + '"]'));
    }

    function limparTelaCompleta(opcoes) {
        if (opcoes.posicaoComLock && opcoes.config) {
            liberarLockPosicaoSilencioso(opcoes.config, opcoes.posicaoComLock);
            opcoes.config.posicaoComLock = '';
        }
        if (opcoes.posicaoInput) opcoes.posicaoInput.value = '';
        if (opcoes.produtoInput) opcoes.produtoInput.value = '';
        ocultarPosicao(opcoes.posicaoConfirm, opcoes.posicaoLabel);
        ocultarDescricao(opcoes.descricaoConfirm, opcoes.produtoDescricao);
        resetarQuantidade(opcoes.quantidadeInput);
        marcarErro(opcoes.posicaoInput, false);
        marcarErro(opcoes.produtoInput, false);
        marcarErro(opcoes.quantidadeInput, false);
        focarCampo(opcoes.posicaoInput);
    }

    function urlPostPocket(config) {
        if (config && config.postUrl) {
            return config.postUrl;
        }
        if (config && config.form) {
            var formUrl = config.form.dataset.postUrl || config.form.getAttribute('action');
            if (formUrl) return formUrl;
        }
        var href = global.location.href.split('#')[0];
        if (href.slice(-1) !== '/') {
            href += '/';
        }
        return href;
    }

    function liberarLockPosicaoSilencioso(config, codigoPosicao) {
        if (!config || !config.csrfToken || !codigoPosicao) return;
        var fd = new FormData();
        fd.append('acao', 'liberar_posicao');
        fd.append('pocket_ajax', '1');
        fd.append('codigo_posicao', codigoPosicao);
        fd.append('csrfmiddlewaretoken', config.csrfToken);
        if (config.skuSelect && config.skuSelect.value) {
            fd.append('sku_id', config.skuSelect.value);
        }
        fetch(urlPostPocket(config), Object.assign({
            method: 'POST',
            body: fd,
        }, opcoesFetchPocket(config.csrfToken))).catch(function () { /* liberação best-effort */ });
    }

    function reservarLockPosicao(config, codigoPosicao, callback) {
        var fd = new FormData();
        fd.append('acao', 'lock_posicao');
        fd.append('pocket_ajax', '1');
        fd.append('codigo_posicao', codigoPosicao);
        fd.append('csrfmiddlewaretoken', config.csrfToken);
        if (config.inventarioId) {
            fd.append('inventario_id', config.inventarioId);
        }
        if (config.skuSelect && config.skuSelect.value) {
            fd.append('sku_id', config.skuSelect.value);
        }
        fetch(urlPostPocket(config), Object.assign({
            method: 'POST',
            body: fd,
        }, opcoesFetchPocket(config.csrfToken)))
            .then(parsearRespostaPocket)
            .then(function (resultado) {
                callback(resultado.ok, resultado.body.message || '', resultado.body || {});
            })
            .catch(function (err) {
                tratarErroFetchPocket(err, function (msg) {
                    callback(false, msg, {});
                });
            });
    }

    function initGeral(config) {
        initAudioTouch();
        var posicaoInput = config.posicaoInput;
        var produtoInput = config.produtoInput;
        var quantidadeInput = config.quantidadeInput;
        var posicaoConfirm = config.posicaoConfirm;
        var posicaoLabel = config.posicaoLabel;
        var descricaoConfirm = config.descricaoConfirm;
        var produtoDescricao = config.produtoDescricao;
        var form = config.form;
        config.mapaPosicoes = config.mapaPosicoes || {};
        config.mapaProdutos = config.mapaProdutos || {};
        config.mapaEan = config.mapaEan || {};
        var mapaPosicoes = config.mapaPosicoes;
        var mapaProdutos = config.mapaProdutos;
        var mapaEan = config.mapaEan;
        var csrfToken = config.csrfToken;
        var btnSalvar = config.btnSalvar;
        config.posicaoComLock = '';

        function telaLimpaOpts() {
            return {
                config: config,
                posicaoComLock: config.posicaoComLock,
                posicaoInput: posicaoInput,
                produtoInput: produtoInput,
                quantidadeInput: quantidadeInput,
                posicaoConfirm: posicaoConfirm,
                posicaoLabel: posicaoLabel,
                descricaoConfirm: descricaoConfirm,
                produtoDescricao: produtoDescricao,
            };
        }

        function resolverProduto(codigo) {
            if (mapaProdutos[codigo]) {
                return { codigo: codigo, descricao: mapaProdutos[codigo] };
            }
            if (mapaEan[codigo]) {
                return {
                    codigo: mapaEan[codigo].codigo_produto || codigo,
                    descricao: mapaEan[codigo].descricao,
                };
            }
            return null;
        }

        var leituraPosicao = null;
        var leituraProduto = null;

        function cancelarLeiturasPendentes() {
            if (leituraPosicao) leituraPosicao.cancelar();
            if (leituraProduto) leituraProduto.cancelar();
        }

        function responderValidacao(callback, ok, detalhe) {
            if (callback) callback(ok, detalhe || null);
        }

        function validarPosicao(silencioso, callback) {
            var codigo = posicaoInput.value.trim();
            if (!codigo) {
                ocultarPosicao(posicaoConfirm, posicaoLabel);
                marcarErro(posicaoInput, false);
                limparMensagemCampo(posicaoInput);
                responderValidacao(callback, false, { vazia: true });
                return false;
            }
            if (config.mapaPosicoes[codigo]) {
                exibirPosicao(config.mapaPosicoes[codigo], posicaoConfirm, posicaoLabel);
                marcarErro(posicaoInput, false);
                limparMensagemCampo(posicaoInput);
                if (!silencioso) {
                    Sons.ok();
                    toast('Posição válida', 'ok');
                }
                responderValidacao(callback, true);
                return true;
            }
            sincronizarDadosMestres(config).then(function (sync) {
                if (!leituraAindaAtual(posicaoInput, codigo)) {
                    responderValidacao(callback, false, { obsoleta: true });
                    return;
                }
                if (!sync.ok) {
                    responderValidacao(callback, false, { rede: true });
                    return;
                }
                var ok = !!config.mapaPosicoes[codigo];
                if (ok) {
                    notificarMestresAtualizados(sync);
                    exibirPosicao(config.mapaPosicoes[codigo], posicaoConfirm, posicaoLabel);
                    marcarErro(posicaoInput, false);
                    limparMensagemCampo(posicaoInput);
                    if (!silencioso) {
                        Sons.ok();
                        toast('Posição válida', 'ok');
                    }
                    responderValidacao(callback, true);
                    return;
                }
                ocultarPosicao(posicaoConfirm, posicaoLabel);
                if (silencioso) {
                    marcarErro(posicaoInput, true);
                } else {
                    sinalizarCodigoInvalido(posicaoInput, 'Posição inválida', Sons.posicaoInvalida);
                }
                responderValidacao(callback, false);
            });
            return false;
        }

        function validarProduto(silencioso, callback) {
            var codigo = produtoInput.value.trim();
            if (!codigo) {
                ocultarDescricao(descricaoConfirm, produtoDescricao);
                marcarErro(produtoInput, false);
                limparMensagemCampo(produtoInput);
                responderValidacao(callback, false, { vazia: true });
                return false;
            }
            var info = resolverProduto(codigo);
            if (info) {
                exibirDescricao(info.descricao, descricaoConfirm, produtoDescricao);
                marcarErro(produtoInput, false);
                limparMensagemCampo(produtoInput);
                if (!silencioso) {
                    Sons.ok();
                    toast('Produto/EAN válido', 'ok');
                }
                responderValidacao(callback, true);
                return true;
            }
            sincronizarDadosMestres(config).then(function (sync) {
                if (!leituraAindaAtual(produtoInput, codigo)) {
                    responderValidacao(callback, false, { obsoleta: true });
                    return;
                }
                if (!sync.ok) {
                    responderValidacao(callback, false, { rede: true });
                    return;
                }
                info = resolverProduto(codigo);
                if (info) {
                    notificarMestresAtualizados(sync);
                    exibirDescricao(info.descricao, descricaoConfirm, produtoDescricao);
                    marcarErro(produtoInput, false);
                    limparMensagemCampo(produtoInput);
                    if (!silencioso) {
                        Sons.ok();
                        toast('Produto/EAN válido', 'ok');
                    }
                    responderValidacao(callback, true);
                    return;
                }
                ocultarDescricao(descricaoConfirm, produtoDescricao);
                if (silencioso) {
                    marcarErro(produtoInput, true);
                } else {
                    sinalizarCodigoInvalido(
                        produtoInput,
                        'Produto/EAN não encontrado',
                        Sons.produtoInvalido
                    );
                }
                responderValidacao(callback, false);
            });
            return false;
        }

        function avancarPosicao(aoTerminar) {
            function terminar(resultado) {
                if (aoTerminar) aoTerminar(resultado);
            }
            var codigo = posicaoInput.value.trim();
            if (!codigo) {
                limparMensagemCampo(posicaoInput);
                marcarErro(posicaoInput, false);
                terminar('vazio');
                return;
            }
            resolverPosicaoComSync(config, codigo, function (ok, alocacao, atualizado, falhaRede) {
                if (!leituraAindaAtual(posicaoInput, codigo)) {
                    terminar('obsoleto');
                    return;
                }
                if (falhaRede) {
                    avisarFalhaRede();
                    terminar('rede');
                    return;
                }
                if (!ok) {
                    ocultarPosicao(posicaoConfirm, posicaoLabel);
                    sinalizarCodigoInvalido(posicaoInput, 'Posição inválida', Sons.posicaoInvalida);
                    terminar('invalido');
                    return;
                }
                if (atualizado) {
                    toast('Dados atualizados automaticamente.', 'ok');
                }
                exibirPosicao(alocacao, posicaoConfirm, posicaoLabel);
                reservarLockPosicao(config, codigo, function (lockOk, msg) {
                    if (!leituraAindaAtual(posicaoInput, codigo)) {
                        if (lockOk) liberarLockPosicaoSilencioso(config, codigo);
                        terminar('obsoleto');
                        return;
                    }
                    if (!lockOk) {
                        if (mensagemEhFalhaRede(msg)) {
                            terminar('rede');
                            return;
                        }
                        var texto = (msg || '').toLowerCase();
                        if (texto.indexOf('inválida') >= 0 || texto.indexOf('invalida') >= 0) {
                            ocultarPosicao(posicaoConfirm, posicaoLabel);
                            sinalizarCodigoInvalido(posicaoInput, 'Posição inválida', Sons.posicaoInvalida);
                            terminar('invalido');
                            return;
                        }
                        if (texto.indexOf('outro operador') >= 0 || texto.indexOf('contagem') >= 0) {
                            Sons.bloqueado();
                        } else {
                            Sons.posicaoInvalida();
                        }
                        toast(msg || 'Posição em contagem por outro operador', 'erro');
                        marcarErro(posicaoInput, true);
                        selecionarCampo(posicaoInput);
                        terminar('bloqueado');
                        return;
                    }
                    config.posicaoComLock = codigo;
                    Sons.ok();
                    toast('Posição válida', 'ok');
                    marcarErro(posicaoInput, false);
                    limparMensagemCampo(posicaoInput);
                    if (focoNoCampo(posicaoInput)) focarCampo(produtoInput);
                    terminar('valido');
                });
            });
        }

        function avancarProduto(aoTerminar) {
            function terminar(resultado) {
                if (aoTerminar) aoTerminar(resultado);
            }
            var codigoProduto = produtoInput.value.trim();
            if (!codigoProduto) {
                limparMensagemCampo(produtoInput);
                marcarErro(produtoInput, false);
                terminar('vazio');
                return;
            }
            validarPosicao(true, function (posOk, detalhe) {
                if (!leituraAindaAtual(produtoInput, codigoProduto)) {
                    terminar('obsoleto');
                    return;
                }
                if (!posOk) {
                    if (detalhe && detalhe.rede) {
                        avisarFalhaRede();
                        terminar('rede');
                        return;
                    }
                    if (detalhe && detalhe.obsoleta) {
                        terminar('obsoleto');
                        return;
                    }
                    sinalizarCodigoInvalido(posicaoInput, 'Posição inválida', Sons.posicaoInvalida);
                    if (focoNoCampo(produtoInput)) focarCampo(posicaoInput);
                    terminar('dependencia');
                    return;
                }
                validarProduto(false, function (prodOk, detalheProd) {
                    if (!leituraAindaAtual(produtoInput, codigoProduto)) {
                        terminar('obsoleto');
                        return;
                    }
                    if (!prodOk) {
                        if (detalheProd && detalheProd.rede) {
                            avisarFalhaRede();
                            terminar('rede');
                            return;
                        }
                        if (detalheProd && detalheProd.obsoleta) {
                            terminar('obsoleto');
                            return;
                        }
                        terminar('invalido');
                        return;
                    }
                    if (focoNoCampo(produtoInput)) focarCampo(quantidadeInput);
                    terminar('valido');
                });
            });
        }

        function limparTudoPosSalvar() {
            cancelarLeiturasPendentes();
            limparTelaCompleta(telaLimpaOpts());
        }

        function liberarEnvio() {
            config._envioEmAndamento = false;
            if (btnSalvar) btnSalvar.disabled = false;
        }

        function enviarContagem() {
            cancelarLeiturasPendentes();
            if (config._envioEmAndamento) return;
            config._envioEmAndamento = true;
            validarPosicao(true, function (posOk, detalhe) {
                if (!posOk) {
                    liberarEnvio();
                    if (detalhe && detalhe.rede) {
                        avisarFalhaRede();
                        return;
                    }
                    if (detalhe && detalhe.obsoleta) return;
                    Sons.posicaoInvalida();
                    toast('Posição inválida', 'erro');
                    mostrarMensagemCampo(posicaoInput, 'Posição inválida');
                    focarCampo(posicaoInput);
                    return;
                }
                validarProduto(true, function (prodOk, detalheProd) {
                    if (!prodOk) {
                        liberarEnvio();
                        if (detalheProd && detalheProd.rede) {
                            avisarFalhaRede();
                            return;
                        }
                        if (detalheProd && detalheProd.obsoleta) return;
                        Sons.produtoInvalido();
                        toast('Produto/EAN não encontrado', 'erro');
                        mostrarMensagemCampo(produtoInput, 'Produto/EAN não encontrado');
                        focarCampo(produtoInput);
                        return;
                    }
                    if (!validarQuantidade(quantidadeInput, false)) {
                        liberarEnvio();
                        focarCampo(quantidadeInput);
                        return;
                    }
                    if (btnSalvar) btnSalvar.disabled = true;

                    if (config.ajax && form) {
                        var fd = new FormData(form);
                        if (config.inventarioId && !fd.get('inventario_id')) {
                            fd.append('inventario_id', config.inventarioId);
                        }
                        fetch(urlPostPocket(config), Object.assign({
                            method: 'POST',
                            body: fd,
                        }, opcoesFetchPocket(csrfToken)))
                            .then(parsearRespostaPocket)
                            .then(function (resultado) {
                                liberarEnvio();
                                if (!resultado.ok) {
                                    var msg = resultado.body.message || 'Erro ao salvar.';
                                    if (resultado.body.errors) {
                                        var chaves = Object.keys(resultado.body.errors);
                                        if (chaves.length) {
                                            msg = resultado.body.errors[chaves[0]][0].message || msg;
                                        }
                                    }
                                    var info = classificarErroServidor(msg);
                                    if (info.titulo.indexOf('bloqueada') >= 0) Sons.bloqueado();
                                    else Sons.erro();
                                    toast(info.titulo, info.tipo);
                                    focarErroServidor(form, resultado.body.errors);
                                    return;
                                }
                                Sons.ok();
                                toast('Contagem registrada', 'ok');
                                limparTudoPosSalvar();
                            })
                            .catch(function (err) {
                                liberarEnvio();
                                Sons.erro();
                                tratarErroFetchPocket(err, toast);
                            });
                        return;
                    }
                    if (form) form.submit();
                    else liberarEnvio();
                });
            });
        }

        leituraPosicao = registrarLeituraCampo(posicaoInput, {
            validar: avancarPosicao,
            aoAlterar: function () {
                limparMensagemCampo(posicaoInput);
                marcarErro(posicaoInput, false);
                var codigo = posicaoInput.value.trim();
                if (config.posicaoComLock && config.posicaoComLock !== codigo) {
                    liberarLockPosicaoSilencioso(config, config.posicaoComLock);
                    config.posicaoComLock = '';
                }
                if (config.posicaoComLock !== codigo) {
                    ocultarPosicao(posicaoConfirm, posicaoLabel);
                }
            },
        });
        leituraProduto = registrarLeituraCampo(produtoInput, {
            validar: avancarProduto,
            aoAlterar: function () {
                limparMensagemCampo(produtoInput);
                marcarErro(produtoInput, false);
                ocultarDescricao(descricaoConfirm, produtoDescricao);
            },
        });
        registrarEnter(quantidadeInput, enviarContagem);

        if (form) {
            form.addEventListener('submit', function (e) {
                e.preventDefault();
                e.stopPropagation();
                enviarContagem();
            });
        }
        global.addEventListener('pagehide', cancelarLeiturasPendentes);

        limparTelaCompleta(telaLimpaOpts());
        iniciarSincronizacaoPeriodica(config);
    }

    function initCiclico(config) {
        initAudioTouch();
        var posicaoInput = config.posicaoInput;
        var produtoInput = config.produtoInput;
        var quantidadeInput = config.quantidadeInput;
        var skuSelect = config.skuSelect;
        var form = config.form;
        config.mapaPosicoes = config.mapaPosicoes || {};
        config.mapaProdutos = config.mapaProdutos || {};
        config.mapaEan = config.mapaEan || {};
        config.mapaSkus = config.mapaSkus || {};
        var csrfToken = config.csrfToken;
        var btnSalvar = config.btnSalvar;
        var callbacks = config.callbacks || {};
        config.posicaoComLock = '';
        config.posicaoValidada = false;
        config.produtoValidado = false;
        config._lockPosicaoEmAndamento = '';
        var leituraPosicao = null;
        var leituraProduto = null;

        function habilitarCampo(input, ativo, placeholderAtivo, placeholderInativo) {
            if (!input) return;
            if (placeholderAtivo || placeholderInativo) {
                input.placeholder = ativo ? placeholderAtivo : placeholderInativo;
            }
        }

        function habilitarProduto(ativo) {
            habilitarCampo(
                produtoInput,
                ativo,
                'Bipar produto ou EAN',
                'Confirmado após a posição'
            );
        }

        function habilitarQuantidade(ativo) {
            habilitarCampo(quantidadeInput, ativo, '', '');
        }

        function resetEstadoCampos() {
            config.posicaoValidada = false;
            config.produtoValidado = false;
            habilitarProduto(false);
            habilitarQuantidade(false);
            if (leituraProduto) leituraProduto.cancelar();
            if (produtoInput) produtoInput.value = '';
            limparMensagemCampo(produtoInput);
            marcarErro(produtoInput, false);
            marcarErro(quantidadeInput, false);
        }

        function cancelarLeiturasPendentes() {
            if (leituraPosicao) leituraPosicao.cancelar();
            if (leituraProduto) leituraProduto.cancelar();
        }

        global.PocketBipagem.resetEstadoCiclico = resetEstadoCampos;

        function obterSkuAtual() {
            if (!skuSelect) return null;
            return config.mapaSkus[String(skuSelect.value)] || null;
        }

        function produtoCorrespondeLote(sku, codigoLido) {
            if (!sku || !codigoLido) return false;
            if (codigoLido === sku.codigo_produto) return true;
            if (sku.codigo_ean && codigoLido === sku.codigo_ean) return true;
            if (config.mapaEan[codigoLido] &&
                config.mapaEan[codigoLido].codigo_produto === sku.codigo_produto) {
                return true;
            }
            if (config.mapaProdutos[codigoLido] !== undefined &&
                codigoLido === sku.codigo_produto) {
                return true;
            }
            return false;
        }

        function telaLimpaOpts() {
            return {
                config: config,
                posicaoComLock: config.posicaoComLock,
                posicaoInput: posicaoInput,
                produtoInput: produtoInput,
                quantidadeInput: quantidadeInput,
            };
        }

        function limparTudoPosSalvar() {
            cancelarLeiturasPendentes();
            limparTelaCompleta(telaLimpaOpts());
            resetEstadoCampos();
            if (callbacks.onLimparConfirmacao) {
                callbacks.onLimparConfirmacao();
            }
        }

        function validarPosicao(silencioso, callback) {
            var codigo = posicaoInput ? posicaoInput.value.trim() : '';
            if (!codigo) {
                config.posicaoValidada = false;
                resetEstadoCampos();
                if (callbacks.onLimparConfirmacao) {
                    callbacks.onLimparConfirmacao();
                }
                marcarErro(posicaoInput, false);
                if (callback) callback(false);
                return false;
            }
            if (config.mapaPosicoes[codigo]) {
                marcarErro(posicaoInput, false);
                if (!silencioso) {
                    Sons.ok();
                    toast('Posição válida', 'ok');
                }
                if (callback) callback(true);
                return true;
            }
            sincronizarDadosMestres(config).then(function (sync) {
                if (!leituraAindaAtual(posicaoInput, codigo)) {
                    if (callback) callback(false, { obsoleta: true });
                    return;
                }
                if (!sync.ok) {
                    if (callback) callback(false, { rede: true });
                    return;
                }
                var ok = !!config.mapaPosicoes[codigo];
                if (ok) {
                    notificarMestresAtualizados(sync);
                    marcarErro(posicaoInput, false);
                    limparMensagemCampo(posicaoInput);
                    if (!silencioso) {
                        Sons.ok();
                        toast('Posição válida', 'ok');
                    }
                    if (callback) callback(true);
                    return;
                }
                config.posicaoValidada = false;
                resetEstadoCampos();
                if (callbacks.onLimparConfirmacao) {
                    callbacks.onLimparConfirmacao();
                }
                if (silencioso) {
                    marcarErro(posicaoInput, true);
                } else {
                    sinalizarCodigoInvalido(posicaoInput, 'Posição inválida', Sons.posicaoInvalida);
                }
                if (callback) callback(false);
            });
            return false;
        }

        function validarProdutoLote(silencioso, callback) {
            var sku = obterSkuAtual();
            var codigo = produtoInput ? produtoInput.value.trim() : '';
            if (!config.posicaoValidada) {
                if (callback) callback(false);
                return false;
            }
            if (!codigo || !sku) {
                config.produtoValidado = false;
                habilitarQuantidade(false);
                marcarErro(produtoInput, !!codigo);
                if (callback) callback(false);
                return false;
            }
            if (produtoCorrespondeLote(sku, codigo)) {
                config.produtoValidado = true;
                marcarErro(produtoInput, false);
                habilitarQuantidade(true);
                if (!silencioso) {
                    Sons.ok();
                    toast('Produto confirmado', 'ok');
                }
                if (callback) callback(true);
                return true;
            }
            sincronizarDadosMestres(config).then(function (sync) {
                if (!leituraAindaAtual(produtoInput, codigo)) {
                    if (callback) callback(false, { obsoleta: true });
                    return;
                }
                if (!sync.ok) {
                    if (callback) callback(false, { rede: true });
                    return;
                }
                var ok = produtoCorrespondeLote(sku, codigo);
                if (ok) {
                    notificarMestresAtualizados(sync);
                    config.produtoValidado = true;
                    marcarErro(produtoInput, false);
                    limparMensagemCampo(produtoInput);
                    habilitarQuantidade(true);
                    if (!silencioso) {
                        Sons.ok();
                        toast('Produto confirmado', 'ok');
                    }
                    if (callback) callback(true);
                    return;
                }
                config.produtoValidado = false;
                habilitarQuantidade(false);
                if (silencioso) {
                    marcarErro(produtoInput, true);
                } else {
                    sinalizarCodigoInvalido(
                        produtoInput,
                        'Produto divergente do SKU selecionado.',
                        Sons.produtoInvalido
                    );
                }
                if (callback) callback(false);
            });
            return false;
        }

        function posicaoJaConfirmadaComLock(codigo) {
            return config.posicaoValidada && config.posicaoComLock === codigo;
        }

        function confirmarPosicaoComLock(codigo, aoTerminar) {
            function terminar(resultado) {
                if (aoTerminar) aoTerminar(resultado);
            }
            if (!codigo) {
                terminar('vazio');
                return;
            }
            if (posicaoJaConfirmadaComLock(codigo)) {
                if (focoNoCampo(posicaoInput)) focarCampo(produtoInput);
                terminar('valido');
                return;
            }
            if (config._lockPosicaoEmAndamento === codigo) {
                return;
            }
            if (config.posicaoComLock && config.posicaoComLock !== codigo) {
                liberarLockPosicaoSilencioso(config, config.posicaoComLock);
                config.posicaoComLock = '';
                config.posicaoValidada = false;
                resetEstadoCampos();
                if (callbacks.onLimparConfirmacao) {
                    callbacks.onLimparConfirmacao();
                }
            }
            config._lockPosicaoEmAndamento = codigo;
            resolverPosicaoComSync(config, codigo, function (ok, alocacao, atualizado, falhaRede) {
                if (!leituraAindaAtual(posicaoInput, codigo)) {
                    config._lockPosicaoEmAndamento = '';
                    terminar('obsoleto');
                    return;
                }
                if (falhaRede) {
                    config._lockPosicaoEmAndamento = '';
                    avisarFalhaRede();
                    terminar('rede');
                    return;
                }
                if (!ok) {
                    config._lockPosicaoEmAndamento = '';
                    config.posicaoValidada = false;
                    resetEstadoCampos();
                    if (callbacks.onLimparConfirmacao) {
                        callbacks.onLimparConfirmacao();
                    }
                    sinalizarCodigoInvalido(posicaoInput, 'Posição inválida', Sons.posicaoInvalida);
                    terminar('invalido');
                    return;
                }
                if (atualizado) {
                    toast('Dados atualizados automaticamente.', 'ok');
                }
                reservarLockPosicao(config, codigo, function (lockOk, msg, payload) {
                    config._lockPosicaoEmAndamento = '';
                    if (!leituraAindaAtual(posicaoInput, codigo)) {
                        if (lockOk) liberarLockPosicaoSilencioso(config, codigo);
                        terminar('obsoleto');
                        return;
                    }
                    if (!lockOk) {
                        if (mensagemEhFalhaRede(msg)) {
                            terminar('rede');
                            return;
                        }
                        var texto = (msg || '').toLowerCase();
                        if (texto.indexOf('inválida') >= 0 || texto.indexOf('invalida') >= 0) {
                            config.posicaoValidada = false;
                            resetEstadoCampos();
                            if (callbacks.onLimparConfirmacao) {
                                callbacks.onLimparConfirmacao();
                            }
                            sinalizarCodigoInvalido(posicaoInput, 'Posição inválida', Sons.posicaoInvalida);
                            terminar('invalido');
                            return;
                        }
                        config.posicaoValidada = false;
                        resetEstadoCampos();
                        if (texto.indexOf('outro operador') >= 0 || texto.indexOf('contagem') >= 0) {
                            Sons.bloqueado();
                        } else {
                            Sons.posicaoInvalida();
                        }
                        toast(msg || 'Posição em contagem por outro operador', 'erro');
                        marcarErro(posicaoInput, true);
                        selecionarCampo(posicaoInput);
                        if (callbacks.onLimparConfirmacao) {
                            callbacks.onLimparConfirmacao();
                        }
                        terminar('bloqueado');
                        return;
                    }
                    if (callbacks.onPosicaoConfirmada) {
                        callbacks.onPosicaoConfirmada(payload.posicao_codigo || codigo, payload.posicao_alocacao || '');
                    }
                    config.posicaoComLock = codigo;
                    config.posicaoValidada = true;
                    config.produtoValidado = false;
                    Sons.ok();
                    toast('Posição válida', 'ok');
                    marcarErro(posicaoInput, false);
                    limparMensagemCampo(posicaoInput);
                    habilitarProduto(true);
                    habilitarQuantidade(false);
                    if (focoNoCampo(posicaoInput)) {
                        if (leituraProduto) leituraProduto.cancelar();
                        if (produtoInput) produtoInput.value = '';
                        limparMensagemCampo(produtoInput);
                        focarCampo(produtoInput);
                    }
                    terminar('valido');
                });
            });
        }

        function avancarPosicao(aoTerminar) {
            var codigo = posicaoInput ? posicaoInput.value.trim() : '';
            confirmarPosicaoComLock(codigo, aoTerminar);
        }

        function avancarProduto(aoTerminar) {
            function terminar(resultado) {
                if (aoTerminar) aoTerminar(resultado);
            }
            var codigoProduto = produtoInput ? produtoInput.value.trim() : '';
            if (!codigoProduto) {
                limparMensagemCampo(produtoInput);
                marcarErro(produtoInput, false);
                terminar('vazio');
                return;
            }
            if (!config.posicaoValidada) {
                sinalizarCodigoInvalido(posicaoInput, 'Posição inválida', Sons.posicaoInvalida);
                if (focoNoCampo(produtoInput)) focarCampo(posicaoInput);
                terminar('dependencia');
                return;
            }
            if (config.produtoValidado && produtoInput.value.trim() === codigoProduto) {
                if (focoNoCampo(produtoInput)) focarCampo(quantidadeInput);
                terminar('valido');
                return;
            }
            validarProdutoLote(false, function (prodOk, detalhe) {
                if (!leituraAindaAtual(produtoInput, codigoProduto)) {
                    terminar('obsoleto');
                    return;
                }
                if (!prodOk) {
                    if (detalhe && detalhe.rede) {
                        avisarFalhaRede();
                        terminar('rede');
                        return;
                    }
                    if (detalhe && detalhe.obsoleta) {
                        terminar('obsoleto');
                        return;
                    }
                    terminar('invalido');
                    return;
                }
                if (focoNoCampo(produtoInput)) focarCampo(quantidadeInput);
                terminar('valido');
            });
        }

        function liberarEnvio() {
            config._envioEmAndamento = false;
            if (btnSalvar) btnSalvar.disabled = false;
        }

        function enviarContagem() {
            // Validação exclusiva do SALVAR (contagem física).
            // FINALIZAR SKU / PRODUTO NÃO ENCONTRADO usam fluxo próprio fora deste form.
            // Um segundo Enter enquanto o POST está em voo é reenvio acidental.
            // Na recontagem o servidor soma a quantidade; o reenvio dobraria o contado.
            cancelarLeiturasPendentes();
            if (config._envioEmAndamento) return;
            var codigoPosicaoAtual = posicaoInput ? posicaoInput.value.trim() : '';
            if (!config.posicaoValidada || !config.posicaoComLock ||
                config.posicaoComLock !== codigoPosicaoAtual) {
                Sons.posicaoInvalida();
                toast('Confirme a posição antes de salvar.', 'erro');
                focarCampo(posicaoInput);
                return;
            }
            config._envioEmAndamento = true;
            validarProdutoLote(true, function (prodOk, detalheProd) {
                if (!prodOk) {
                    liberarEnvio();
                    if (detalheProd && detalheProd.rede) {
                        avisarFalhaRede();
                        return;
                    }
                    if (detalheProd && detalheProd.obsoleta) return;
                    Sons.produtoInvalido();
                    toast('Produto divergente do SKU selecionado.', 'erro');
                    mostrarMensagemCampo(produtoInput, 'Produto divergente do SKU selecionado.');
                    focarCampo(produtoInput);
                    return;
                }
                if (!validarQuantidade(quantidadeInput, false)) {
                    liberarEnvio();
                    focarCampo(quantidadeInput);
                    return;
                }
                if (btnSalvar) btnSalvar.disabled = true;

                var bodyContagem = new FormData(form);
                if (!bodyContagem.get('pocket_ajax')) {
                    bodyContagem.append('pocket_ajax', '1');
                }
                fetch(global.location.href, Object.assign({
                    method: 'POST',
                    body: bodyContagem,
                }, opcoesFetchPocket(csrfToken)))
                    .then(parsearRespostaPocket)
                    .then(function (resultado) {
                        liberarEnvio();
                        if (!resultado.ok) {
                            var msg = resultado.body.message || 'Erro ao salvar.';
                            if (resultado.body.errors) {
                                var chaves = Object.keys(resultado.body.errors);
                                if (chaves.length) {
                                    msg = resultado.body.errors[chaves[0]][0].message || msg;
                                }
                            }
                            var info = classificarErroServidor(msg);
                            if (info.titulo.indexOf('bloqueada') >= 0) Sons.bloqueado();
                            else if (info.titulo.indexOf('Produto divergente') >= 0) {
                                Sons.produtoInvalido();
                            } else Sons.erro();
                            toast(info.titulo, info.tipo);
                            focarErroServidor(form, resultado.body.errors);
                            return;
                        }
                        var b = resultado.body;
                        Sons.ok();
                        if (b.tipo_mensagem === 'warning') {
                            toast('Divergência encontrada', 'alerta');
                        } else {
                            toast('Contagem registrada', 'ok');
                        }
                        processarSucessoPocket(
                            callbacks.onSucesso,
                            b,
                            limparTudoPosSalvar,
                            toast
                        );
                    })
                    .catch(function (err) {
                        liberarEnvio();
                        Sons.erro();
                        tratarErroFetchPocket(err, toast);
                    });
            });
        }

        leituraPosicao = registrarLeituraCampo(posicaoInput, {
            tabConfirma: true,
            validar: avancarPosicao,
            aoAlterar: function () {
                if (!posicaoInput) return;
                limparMensagemCampo(posicaoInput);
                marcarErro(posicaoInput, false);
                var codigo = posicaoInput.value.trim();
                if (!codigo) {
                    if (config.posicaoComLock) {
                        liberarLockPosicaoSilencioso(config, config.posicaoComLock);
                        config.posicaoComLock = '';
                    }
                    config.posicaoValidada = false;
                    resetEstadoCampos();
                    if (callbacks.onLimparConfirmacao) {
                        callbacks.onLimparConfirmacao();
                    }
                    return;
                }
                if (config.posicaoComLock && config.posicaoComLock !== codigo) {
                    liberarLockPosicaoSilencioso(config, config.posicaoComLock);
                    config.posicaoComLock = '';
                    config.posicaoValidada = false;
                    resetEstadoCampos();
                    if (callbacks.onLimparConfirmacao) {
                        callbacks.onLimparConfirmacao();
                    }
                }
            },
        });
        leituraProduto = registrarLeituraCampo(produtoInput, {
            tabConfirma: true,
            validar: avancarProduto,
            aoAlterar: function () {
                config.produtoValidado = false;
                habilitarQuantidade(false);
                marcarErro(produtoInput, false);
                limparMensagemCampo(produtoInput);
            },
        });
        registrarEnter(quantidadeInput, enviarContagem);
        if (form) {
            form.addEventListener('submit', function (e) {
                e.preventDefault();
                e.stopPropagation();
                // Somente SALVAR (contagem física) passa por aqui.
                enviarContagem();
            });
        }
        global.addEventListener('pagehide', cancelarLeiturasPendentes);
        if (skuSelect) {
            skuSelect.addEventListener('change', function () {
                cancelarLeiturasPendentes();
                if (callbacks.atualizarSku) {
                    callbacks.atualizarSku(config.mapaSkus[skuSelect.value]);
                }
                limparTelaCompleta(telaLimpaOpts());
                resetEstadoCampos();
                if (callbacks.onLimparConfirmacao) {
                    callbacks.onLimparConfirmacao();
                }
            });
        }

        limparTelaCompleta(telaLimpaOpts());
        resetEstadoCampos();
        if (callbacks.onLimparConfirmacao) {
            callbacks.onLimparConfirmacao();
        }
        iniciarSincronizacaoPeriodica(config);
    }

    global.PocketBipagem = {
        Sons: Sons,
        initGeral: initGeral,
        initCiclico: initCiclico,
        toast: toast,
        focarCampo: focarCampo,
        parsearRespostaPocket: parsearRespostaPocket,
        opcoesFetchPocket: opcoesFetchPocket,
        tratarErroFetchPocket: function (err) { tratarErroFetchPocket(err, toast); },
        criarControleValidacaoLeitura: criarControleValidacaoLeitura,
        POCKET_VALIDACAO_ATRASO_MS: POCKET_VALIDACAO_ATRASO_MS,
    };
}(window));
