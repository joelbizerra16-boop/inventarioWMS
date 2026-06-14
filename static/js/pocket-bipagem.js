/**
 * Pocket Bipagem — coletor WMS (foco em bipagem contínua).
 */
(function (global) {
    'use strict';

    var audioCtx = null;
    var toastTimer = null;
    var TOAST_MS = 2600;

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
        global.requestAnimationFrame(function () {
            el.focus();
            if (selecionar !== false && el.select) el.select();
        });
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

    function registrarEnter(campo, callback) {
        if (!campo) return;
        campo.addEventListener('keydown', function (evento) {
            if (evento.key === 'Enter') {
                evento.preventDefault();
                callback();
            }
        });
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
            var ok = !!config.mapaPosicoes[codigo];
            callback(ok, ok ? config.mapaPosicoes[codigo] : null, sync.alterado && ok);
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
        fd.append('codigo_posicao', codigoPosicao);
        fd.append('csrfmiddlewaretoken', config.csrfToken);
        if (config.skuSelect && config.skuSelect.value) {
            fd.append('sku_id', config.skuSelect.value);
        }
        fetch(urlPostPocket(config), {
            method: 'POST',
            body: fd,
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': config.csrfToken,
            },
        }).catch(function () { /* liberação best-effort */ });
    }

    function reservarLockPosicao(config, codigoPosicao, callback) {
        var fd = new FormData();
        fd.append('acao', 'lock_posicao');
        fd.append('codigo_posicao', codigoPosicao);
        fd.append('csrfmiddlewaretoken', config.csrfToken);
        if (config.inventarioId) {
            fd.append('inventario_id', config.inventarioId);
        }
        if (config.skuSelect && config.skuSelect.value) {
            fd.append('sku_id', config.skuSelect.value);
        }
        fetch(urlPostPocket(config), {
            method: 'POST',
            body: fd,
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': config.csrfToken,
            },
        })
            .then(function (res) {
                return res.json().then(function (body) {
                    return { ok: res.ok, body: body };
                });
            })
            .then(function (resultado) {
                callback(resultado.ok, resultado.body.message || '');
            })
            .catch(function () {
                callback(false, 'Sem conexão');
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

        function validarPosicao(silencioso, callback) {
            var codigo = posicaoInput.value.trim();
            if (!codigo) {
                ocultarPosicao(posicaoConfirm, posicaoLabel);
                marcarErro(posicaoInput, false);
                if (callback) callback(false);
                return false;
            }
            if (config.mapaPosicoes[codigo]) {
                exibirPosicao(config.mapaPosicoes[codigo], posicaoConfirm, posicaoLabel);
                marcarErro(posicaoInput, false);
                if (!silencioso) {
                    Sons.ok();
                    toast('Posição válida', 'ok');
                }
                if (callback) callback(true);
                return true;
            }
            sincronizarDadosMestres(config).then(function (sync) {
                var ok = !!config.mapaPosicoes[codigo];
                if (ok) {
                    notificarMestresAtualizados(sync);
                    exibirPosicao(config.mapaPosicoes[codigo], posicaoConfirm, posicaoLabel);
                    marcarErro(posicaoInput, false);
                    if (!silencioso) {
                        Sons.ok();
                        toast('Posição válida', 'ok');
                    }
                    if (callback) callback(true);
                    return;
                }
                ocultarPosicao(posicaoConfirm, posicaoLabel);
                marcarErro(posicaoInput, true);
                if (!silencioso) {
                    Sons.posicaoInvalida();
                    toast('Posição inválida', 'erro');
                }
                if (callback) callback(false);
            });
            return false;
        }

        function validarProduto(silencioso, callback) {
            var codigo = produtoInput.value.trim();
            if (!codigo) {
                ocultarDescricao(descricaoConfirm, produtoDescricao);
                marcarErro(produtoInput, false);
                if (callback) callback(false);
                return false;
            }
            var info = resolverProduto(codigo);
            if (info) {
                exibirDescricao(info.descricao, descricaoConfirm, produtoDescricao);
                marcarErro(produtoInput, false);
                if (!silencioso) {
                    Sons.ok();
                    toast('Produto/EAN válido', 'ok');
                }
                if (callback) callback(true);
                return true;
            }
            sincronizarDadosMestres(config).then(function (sync) {
                info = resolverProduto(codigo);
                if (info) {
                    notificarMestresAtualizados(sync);
                    exibirDescricao(info.descricao, descricaoConfirm, produtoDescricao);
                    marcarErro(produtoInput, false);
                    if (!silencioso) {
                        Sons.ok();
                        toast('Produto/EAN válido', 'ok');
                    }
                    if (callback) callback(true);
                    return;
                }
                ocultarDescricao(descricaoConfirm, produtoDescricao);
                marcarErro(produtoInput, true);
                if (!silencioso) {
                    Sons.produtoInvalido();
                    toast('Produto/EAN não encontrado', 'erro');
                }
                if (callback) callback(false);
            });
            return false;
        }

        function avancarPosicao() {
            var codigo = posicaoInput.value.trim();
            if (!codigo) {
                validarPosicao(false);
                focarCampo(posicaoInput);
                return;
            }
            resolverPosicaoComSync(config, codigo, function (ok, alocacao, atualizado) {
                if (!ok) {
                    ocultarPosicao(posicaoConfirm, posicaoLabel);
                    marcarErro(posicaoInput, true);
                    Sons.posicaoInvalida();
                    toast('Posição inválida', 'erro');
                    focarCampo(posicaoInput);
                    return;
                }
                if (atualizado) {
                    toast('Dados atualizados automaticamente.', 'ok');
                }
                exibirPosicao(alocacao, posicaoConfirm, posicaoLabel);
                reservarLockPosicao(config, codigo, function (lockOk, msg) {
                    if (!lockOk) {
                        if (msg.indexOf('outro operador') >= 0 || msg.indexOf('contagem') >= 0) {
                            Sons.bloqueado();
                        } else {
                            Sons.posicaoInvalida();
                        }
                        toast(msg || 'Posição em contagem por outro operador', 'erro');
                        marcarErro(posicaoInput, true);
                        focarCampo(posicaoInput);
                        return;
                    }
                    config.posicaoComLock = codigo;
                    Sons.ok();
                    toast('Posição válida', 'ok');
                    marcarErro(posicaoInput, false);
                    focarCampo(produtoInput);
                });
            });
        }

        function avancarProduto() {
            validarPosicao(true, function (posOk) {
                if (!posOk) {
                    Sons.posicaoInvalida();
                    toast('Posição inválida', 'erro');
                    focarCampo(posicaoInput);
                    return;
                }
                validarProduto(false, function (prodOk) {
                    if (!prodOk) {
                        focarCampo(produtoInput);
                        return;
                    }
                    focarCampo(quantidadeInput);
                });
            });
        }

        function limparTudoPosSalvar() {
            limparTelaCompleta(telaLimpaOpts());
        }

        function enviarContagem() {
            validarPosicao(true, function (posOk) {
                if (!posOk) {
                    Sons.posicaoInvalida();
                    toast('Posição inválida', 'erro');
                    focarCampo(posicaoInput);
                    return;
                }
                validarProduto(true, function (prodOk) {
                    if (!prodOk) {
                        Sons.produtoInvalido();
                        toast('Produto/EAN não encontrado', 'erro');
                        focarCampo(produtoInput);
                        return;
                    }
                    if (!validarQuantidade(quantidadeInput, false)) {
                        focarCampo(quantidadeInput);
                        return;
                    }
                    if (btnSalvar) btnSalvar.disabled = true;

                    if (config.ajax && form) {
                        var fd = new FormData(form);
                        if (config.inventarioId && !fd.get('inventario_id')) {
                            fd.append('inventario_id', config.inventarioId);
                        }
                        fetch(urlPostPocket(config), {
                            method: 'POST',
                            body: fd,
                            headers: {
                                'X-Requested-With': 'XMLHttpRequest',
                                'X-CSRFToken': csrfToken || '',
                            },
                        })
                            .then(function (res) {
                                return res.json().then(function (body) {
                                    return { ok: res.ok, body: body };
                                });
                            })
                            .then(function (resultado) {
                                if (btnSalvar) btnSalvar.disabled = false;
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
                                    return;
                                }
                                Sons.ok();
                                toast('Contagem registrada', 'ok');
                                limparTudoPosSalvar();
                            })
                            .catch(function () {
                                if (btnSalvar) btnSalvar.disabled = false;
                                Sons.erro();
                                toast('Sem conexão', 'erro');
                            });
                        return;
                    }
                    if (form) form.submit();
                });
            });
        }

        registrarEnter(posicaoInput, avancarPosicao);
        registrarEnter(produtoInput, avancarProduto);
        registrarEnter(quantidadeInput, enviarContagem);
        posicaoInput.addEventListener('input', function () {
            if (!posicaoInput.value.trim()) {
                ocultarPosicao(posicaoConfirm, posicaoLabel);
            } else {
                validarPosicao(true);
            }
        });
        produtoInput.addEventListener('input', function () {
            if (!produtoInput.value.trim()) {
                ocultarDescricao(descricaoConfirm, produtoDescricao);
            } else {
                validarProduto(true);
            }
        });

        if (form) {
            form.addEventListener('submit', function (e) {
                e.preventDefault();
                enviarContagem();
            });
        }

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

        function habilitarCampo(input, ativo, placeholderAtivo, placeholderInativo) {
            if (!input) return;
            input.disabled = !ativo;
            input.readOnly = !ativo;
            input.classList.toggle('pocket-input--readonly', !ativo);
            input.tabIndex = ativo ? 0 : -1;
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
            if (produtoInput) produtoInput.value = '';
            marcarErro(produtoInput, false);
            marcarErro(quantidadeInput, false);
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
                var ok = !!config.mapaPosicoes[codigo];
                if (ok) {
                    notificarMestresAtualizados(sync);
                    marcarErro(posicaoInput, false);
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
                marcarErro(posicaoInput, true);
                if (!silencioso) {
                    Sons.posicaoInvalida();
                    toast('Posição inválida', 'erro');
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
                var ok = produtoCorrespondeLote(sku, codigo);
                if (ok) {
                    notificarMestresAtualizados(sync);
                    config.produtoValidado = true;
                    marcarErro(produtoInput, false);
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
                marcarErro(produtoInput, true);
                if (!silencioso) {
                    Sons.produtoInvalido();
                    toast('Produto divergente do SKU selecionado.', 'erro');
                }
                if (callback) callback(false);
            });
            return false;
        }

        function posicaoJaConfirmadaComLock(codigo) {
            return config.posicaoValidada && config.posicaoComLock === codigo;
        }

        function confirmarPosicaoComLock(codigo) {
            if (!codigo) {
                return;
            }
            if (posicaoJaConfirmadaComLock(codigo)) {
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
            resolverPosicaoComSync(config, codigo, function (ok, alocacao, atualizado) {
                if (!ok) {
                    config._lockPosicaoEmAndamento = '';
                    config.posicaoValidada = false;
                    resetEstadoCampos();
                    if (callbacks.onLimparConfirmacao) {
                        callbacks.onLimparConfirmacao();
                    }
                    marcarErro(posicaoInput, true);
                    Sons.posicaoInvalida();
                    toast('Posição inválida', 'erro');
                    focarCampo(posicaoInput);
                    return;
                }
                if (atualizado) {
                    toast('Dados atualizados automaticamente.', 'ok');
                }
                reservarLockPosicao(config, codigo, function (lockOk, msg) {
                    config._lockPosicaoEmAndamento = '';
                    if (!lockOk) {
                        config.posicaoValidada = false;
                        resetEstadoCampos();
                        if (msg.indexOf('outro operador') >= 0 || msg.indexOf('contagem') >= 0) {
                            Sons.bloqueado();
                        } else {
                            Sons.posicaoInvalida();
                        }
                        toast(msg || 'Posição em contagem por outro operador', 'erro');
                        marcarErro(posicaoInput, true);
                        if (callbacks.onLimparConfirmacao) {
                            callbacks.onLimparConfirmacao();
                        }
                        focarCampo(posicaoInput);
                        return;
                    }
                    config.posicaoComLock = codigo;
                    config.posicaoValidada = true;
                    config.produtoValidado = false;
                    Sons.ok();
                    toast('Posição válida', 'ok');
                    marcarErro(posicaoInput, false);
                    habilitarProduto(true);
                    habilitarQuantidade(false);
                    if (produtoInput) produtoInput.value = '';
                    focarCampo(produtoInput);
                });
            });
        }

        function avancarPosicao() {
            var codigo = posicaoInput ? posicaoInput.value.trim() : '';
            if (!codigo) {
                validarPosicao(false);
                focarCampo(posicaoInput);
                return;
            }
            if (posicaoJaConfirmadaComLock(codigo)) {
                focarCampo(produtoInput);
                return;
            }
            confirmarPosicaoComLock(codigo);
        }

        function avancarProduto() {
            if (!config.posicaoValidada) {
                Sons.posicaoInvalida();
                toast('Posição inválida', 'erro');
                focarCampo(posicaoInput);
                return;
            }
            validarProdutoLote(false, function (prodOk) {
                if (!prodOk) {
                    focarCampo(produtoInput);
                    return;
                }
                focarCampo(quantidadeInput);
            });
        }

        function enviarContagem() {
            if (!config.posicaoValidada || !config.posicaoComLock) {
                Sons.posicaoInvalida();
                toast('Confirme a posição antes de salvar.', 'erro');
                focarCampo(posicaoInput);
                return;
            }
            validarProdutoLote(true, function (prodOk) {
                if (!prodOk) {
                    Sons.produtoInvalido();
                    toast('Produto divergente do SKU selecionado.', 'erro');
                    focarCampo(produtoInput);
                    return;
                }
                if (!validarQuantidade(quantidadeInput, false)) {
                    focarCampo(quantidadeInput);
                    return;
                }
                if (btnSalvar) btnSalvar.disabled = true;
                if (produtoInput) produtoInput.disabled = false;
                if (quantidadeInput) quantidadeInput.disabled = false;

                fetch(global.location.href, {
                    method: 'POST',
                    body: new FormData(form),
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest',
                        'X-CSRFToken': csrfToken || '',
                    },
                })
                    .then(function (res) {
                        return res.json().then(function (body) {
                            return { ok: res.ok, body: body };
                        });
                    })
                    .then(function (resultado) {
                        if (btnSalvar) btnSalvar.disabled = false;
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
                            return;
                        }
                        var b = resultado.body;
                        Sons.ok();
                        if (b.tipo_mensagem === 'warning') {
                            toast('Divergência encontrada', 'alerta');
                        } else {
                            toast('Contagem registrada', 'ok');
                        }
                        if (callbacks.onSucesso) callbacks.onSucesso(b);
                        limparTudoPosSalvar();
                    })
                    .catch(function () {
                        if (btnSalvar) btnSalvar.disabled = false;
                        Sons.erro();
                        toast('Sem conexão', 'erro');
                    });
            });
        }

        registrarEnter(posicaoInput, avancarPosicao);
        registrarEnter(produtoInput, avancarProduto);
        registrarEnter(quantidadeInput, enviarContagem);
        var posicaoInputDebounceTimer = null;
        if (posicaoInput) {
            posicaoInput.addEventListener('input', function () {
                var codigo = posicaoInput.value.trim();
                if (posicaoInputDebounceTimer) {
                    global.clearTimeout(posicaoInputDebounceTimer);
                    posicaoInputDebounceTimer = null;
                }
                if (!codigo) {
                    config.posicaoValidada = false;
                    resetEstadoCampos();
                    if (callbacks.onLimparConfirmacao) {
                        callbacks.onLimparConfirmacao();
                    }
                    return;
                }
                if (posicaoJaConfirmadaComLock(codigo)) {
                    return;
                }
                posicaoInputDebounceTimer = global.setTimeout(function () {
                    posicaoInputDebounceTimer = null;
                    var codigoAtual = posicaoInput.value.trim();
                    if (!codigoAtual || posicaoJaConfirmadaComLock(codigoAtual)) {
                        return;
                    }
                    confirmarPosicaoComLock(codigoAtual);
                }, 150);
            });
        }
        if (produtoInput) {
            produtoInput.addEventListener('input', function () {
                config.produtoValidado = false;
                habilitarQuantidade(false);
                if (!produtoInput.value.trim()) {
                    marcarErro(produtoInput, false);
                } else if (config.posicaoValidada) {
                    validarProdutoLote(true);
                }
            });
        }
        if (form) {
            form.addEventListener('submit', function (e) {
                e.preventDefault();
                enviarContagem();
            });
        }
        if (skuSelect) {
            skuSelect.addEventListener('change', function () {
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
    };
}(window));
