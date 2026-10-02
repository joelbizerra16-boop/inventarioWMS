/**
 * Simulação de wedge (teclas rápidas) contra o JS real do Pocket.
 * Não é leitura física de coletor. Não grava código nenhum.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const raiz = path.resolve(__dirname, '..');
const falhas = [];

function assert(cond, msg) {
    if (!cond) falhas.push(msg);
}

function classeDe(texto) {
    return String(texto || '').split(/\s+/).filter(Boolean);
}

function combina(el, seletor) {
    if (!el || !el.tagName) return false;
    return seletor.split(',').some(function (parte) {
        parte = parte.trim();
        if (!parte) return false;
        const attr = parte.match(/^([a-zA-Z]+)?(?:\.([a-zA-Z0-9_-]+))?\[([a-zA-Z0-9_-]+)="([^"]*)"\]$/);
        const simples = parte.match(/^([a-zA-Z]+)?(?:\.([a-zA-Z0-9_-]+))?$/);
        const id = parte.match(/^#([a-zA-Z0-9_-]+)$/);
        const soClasse = parte.match(/^\.([a-zA-Z0-9_-]+)$/);
        if (id) return el.id === id[1];
        if (soClasse) return classeDe(el.className).indexOf(soClasse[1]) >= 0;
        const alvo = attr || simples;
        if (!alvo) return false;
        if (alvo[1] && el.tagName.toLowerCase() !== alvo[1].toLowerCase()) return false;
        if (alvo[2] && classeDe(el.className).indexOf(alvo[2]) < 0) return false;
        if (attr && el.getAttribute(attr[3]) !== attr[4]) return false;
        if (!alvo[1] && !alvo[2] && !attr) return false;
        return true;
    });
}

function criarDocumento() {
    const nos = [];

    function criar(tag, attrs) {
        const el = {
            tagName: String(tag).toUpperCase(),
            id: '',
            className: '',
            type: tag === 'input' ? 'text' : '',
            name: '',
            value: '',
            disabled: false,
            readOnly: false,
            hidden: false,
            selectionStart: 0,
            selectionEnd: 0,
            parentNode: null,
            children: [],
            attributes: {},
            dataset: {},
            _listeners: [],
            textContent: '',
        };
        el.classList = {
            add: function (c) {
                const lista = classeDe(el.className);
                if (lista.indexOf(c) < 0) lista.push(c);
                el.className = lista.join(' ');
            },
            remove: function (c) {
                el.className = classeDe(el.className).filter(function (item) {
                    return item !== c;
                }).join(' ');
            },
            toggle: function (c, forcar) {
                const tem = classeDe(el.className).indexOf(c) >= 0;
                const ligado = forcar === undefined ? !tem : !!forcar;
                if (ligado) el.classList.add(c);
                else el.classList.remove(c);
            },
            contains: function (c) {
                return classeDe(el.className).indexOf(c) >= 0;
            },
        };
        el.setAttribute = function (nome, valor) {
            el.attributes[nome] = String(valor);
            if (nome === 'id') el.id = String(valor);
            if (nome === 'class') el.className = String(valor);
            if (nome === 'type') el.type = String(valor);
            if (nome === 'maxlength') el.attributes.maxlength = String(valor);
        };
        el.getAttribute = function (nome) {
            return Object.prototype.hasOwnProperty.call(el.attributes, nome) ? el.attributes[nome] : null;
        };
        el.removeAttribute = function (nome) {
            delete el.attributes[nome];
        };
        el.addEventListener = function (tipo, fn, captura) {
            el._listeners.push({
                tipo: tipo,
                fn: fn,
                captura: captura === true || !!(captura && captura.capture),
            });
        };
        el.removeEventListener = function () {};
        el.setSelectionRange = function (inicio, fim) {
            el.selectionStart = inicio;
            el.selectionEnd = fim;
        };
        el.select = function () {
            el.selectionStart = 0;
            el.selectionEnd = (el.value || '').length;
        };
        el.focus = function () {
            if (el.disabled) return;
            documento.activeElement = el;
            el.dispatchEvent(evento('focus', {}));
        };
        el.contains = function (outro) {
            let no = outro;
            while (no) {
                if (no === el) return true;
                no = no.parentNode;
            }
            return false;
        };
        el.closest = function (seletor) {
            let no = el;
            while (no) {
                if (combina(no, seletor)) return no;
                no = no.parentNode;
            }
            return null;
        };
        el.querySelectorAll = function (seletor) {
            return descendentes(el).filter(function (no) {
                return combina(no, seletor);
            });
        };
        el.querySelector = function (seletor) {
            return el.querySelectorAll(seletor)[0] || null;
        };
        el.dispatchEvent = function (ev) {
            despachar(el, ev);
            return !ev.defaultPrevented;
        };
        el.appendChild = function (filho) {
            filho.parentNode = el;
            el.children.push(filho);
            return filho;
        };
        if (attrs) {
            Object.keys(attrs).forEach(function (chave) {
                if (chave === 'class') el.className = attrs[chave];
                else if (chave === 'text') el.textContent = attrs[chave];
                else el[chave] = attrs[chave];
                if (chave === 'id' || chave === 'maxlength' || chave === 'type') {
                    el.setAttribute(chave === 'maxlength' ? 'maxlength' : chave, attrs[chave]);
                }
            });
        }
        nos.push(el);
        return el;
    }

    function descendentes(raiz) {
        const saida = [];
        function andar(no) {
            (no.children || []).forEach(function (filho) {
                saida.push(filho);
                andar(filho);
            });
        }
        andar(raiz);
        return saida;
    }

    function despachar(alvo, ev) {
        ev.target = alvo;
        const cadeia = [];
        let no = alvo;
        while (no) {
            cadeia.unshift(no);
            no = no.parentNode;
        }
        function invocar(node, captura) {
            (node._listeners || []).forEach(function (ouvinte) {
                if (ev.propagationStopped) return;
                if (ouvinte.tipo === ev.type && ouvinte.captura === captura) {
                    ouvinte.fn.call(node, ev);
                }
            });
        }
        cadeia.forEach(function (node) {
            if (!ev.propagationStopped) invocar(node, true);
        });
        for (let i = cadeia.length - 1; i >= 0; i -= 1) {
            if (ev.propagationStopped) break;
            invocar(cadeia[i], false);
        }
        if (!ev.defaultPrevented && ev.type === 'keydown' && alvo.value !== undefined) {
            aplicarTeclaNativa(alvo, ev);
        }
    }

    function aplicarTeclaNativa(el, ev) {
        const valor = el.value || '';
        let inicio = el.selectionStart;
        let fim = el.selectionEnd;
        if (inicio == null) inicio = valor.length;
        if (fim == null) fim = valor.length;
        if (ev.key && ev.key.length === 1 && !ev.ctrlKey && !ev.altKey && !ev.metaKey) {
            el.value = valor.slice(0, inicio) + ev.key + valor.slice(fim);
            const cursor = inicio + 1;
            el.setSelectionRange(cursor, cursor);
            el.dispatchEvent(evento('input', {}));
            return;
        }
        if (ev.key === 'Backspace' && inicio === fim && inicio > 0) {
            el.value = valor.slice(0, inicio - 1) + valor.slice(fim);
            el.setSelectionRange(inicio - 1, inicio - 1);
            el.dispatchEvent(evento('input', {}));
        } else if (ev.key === 'Backspace' && inicio !== fim) {
            el.value = valor.slice(0, inicio) + valor.slice(fim);
            el.setSelectionRange(inicio, inicio);
            el.dispatchEvent(evento('input', {}));
        }
    }

    const documento = {
        nodeType: 9,
        readyState: 'loading',
        activeElement: null,
        body: null,
        documentElement: null,
        _listeners: [],
        children: [],
        parentNode: null,
    };
    documento.createElement = function (tag) {
        return criar(tag);
    };
    documento.addEventListener = function (tipo, fn, captura) {
        documento._listeners.push({
            tipo: tipo,
            fn: fn,
            captura: captura === true || !!(captura && captura.capture),
        });
    };
    documento.removeEventListener = function () {};
    documento.getElementById = function (id) {
        if (documento.id === id) return documento;
        return descendentes(documento).find(function (no) { return no.id === id; }) || null;
    };
    documento.querySelectorAll = function (seletor) {
        return descendentes(documento).filter(function (no) { return combina(no, seletor); });
    };
    documento.querySelector = function (seletor) {
        return documento.querySelectorAll(seletor)[0] || null;
    };
    documento.contains = function (outro) {
        if (outro === documento) return true;
        let no = outro;
        while (no) {
            if (no === documento) return true;
            no = no.parentNode;
        }
        return false;
    };
    documento.dispatchEvent = function (ev) {
        despachar(documento, ev);
    };

    const corpo = criar('body');
    corpo.parentNode = documento;
    documento.body = corpo;
    documento.children = [corpo];
    documento.activeElement = corpo;
    documento.criar = criar;
    return documento;
}

function evento(tipo, campos) {
    const ev = {
        type: tipo,
        key: '',
        keyCode: 0,
        which: 0,
        bubbles: true,
        cancelable: true,
        defaultPrevented: false,
        propagationStopped: false,
        ctrlKey: false,
        altKey: false,
        metaKey: false,
        isComposing: false,
        target: null,
        preventDefault: function () { ev.defaultPrevented = true; },
        stopPropagation: function () { ev.propagationStopped = true; },
    };
    Object.keys(campos || {}).forEach(function (chave) {
        ev[chave] = campos[chave];
    });
    if (ev.key === 'Enter') {
        ev.keyCode = 13;
        ev.which = 13;
    }
    return ev;
}

function bip(el, texto, opcoes) {
    opcoes = opcoes || {};
    el.focus();
    if (opcoes.selecionar) el.select();
    String(texto).split('').forEach(function (caractere) {
        if (opcoes.selecaoEstagnada) {
            el.selectionStart = 0;
            el.selectionEnd = (el.value || '').length;
        }
        el.dispatchEvent(evento('keydown', { key: caractere }));
        el.dispatchEvent(evento('keyup', { key: caractere }));
    });
}

function carregar(sandbox, arquivos) {
    arquivos.forEach(function (relativo) {
        const codigo = fs.readFileSync(path.join(raiz, relativo), 'utf8');
        vm.runInContext(codigo, sandbox, { filename: relativo });
    });
}

function sandboxDe(documento, extras) {
    const sandbox = {
        console: { info: function () {}, error: function () {}, log: function () {} },
        setTimeout: function (fn) {
            return setTimeout(fn, 0);
        },
        clearTimeout: function (id) { clearTimeout(id); },
        setInterval: function () { return 0; },
        clearInterval: function () {},
        Promise: Promise,
        AbortController: global.AbortController,
        Event: function Event(tipo) {
            return evento(tipo, {});
        },
        document: documento,
    };
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    sandbox.addEventListener = function (tipo, fn) {
        sandbox._ouvintes = sandbox._ouvintes || [];
        sandbox._ouvintes.push({ tipo: tipo, fn: fn });
    };
    sandbox.removeEventListener = function () {};
    Object.assign(sandbox, extras || {});
    vm.createContext(sandbox);
    return sandbox;
}

function drenar() {
    return new Promise(function (resolve) { setTimeout(resolve, 0); });
}

function pronto(documento, sandbox) {
    documento.readyState = 'complete';
    documento.dispatchEvent(evento('DOMContentLoaded', {}));
    sandbox.document = documento;
}

function simularCadastro() {
    const doc = criarDocumento();
    const toast = doc.criar('div', { id: 'pocket-toast' });
    const teclado = doc.criar('button', { id: 'pocket-cadastro-teclado', type: 'button' });
    const form = doc.criar('form', { id: 'pocket-precadastro-posicao-form', class: 'pocket-cadastro-form' });
    form.dataset.sucesso = '0';
    form.dataset.mensagem = '';
    form.dataset.validarCodigoUrl = '/validar';
    form.className = 'pocket-cadastro-form';
    const grupoCodigo = doc.criar('div', { class: 'mb-3' });
    const codigo = doc.criar('input', { id: 'id_codigo', name: 'codigo', maxlength: '50' });
    codigo.setAttribute('data-cadastro-enter', 'validar-codigo');
    const grupoPosicao = doc.criar('div', { class: 'mb-3' });
    const posicao = doc.criar('input', { id: 'id_posicao', name: 'posicao', maxlength: '100' });
    const salvar = doc.criar('button', { type: 'submit' });
    salvar.setAttribute('type', 'submit');
    grupoCodigo.appendChild(codigo);
    grupoPosicao.appendChild(posicao);
    form.appendChild(grupoCodigo);
    form.appendChild(grupoPosicao);
    form.appendChild(salvar);
    doc.body.appendChild(toast);
    doc.body.appendChild(teclado);
    doc.body.appendChild(form);

    let envios = 0;
    let consultas = 0;
    form.addEventListener('submit', function () { envios += 1; });
    const sandbox = sandboxDe(doc, {
        fetch: function () {
            consultas += 1;
            return Promise.resolve({
                ok: true,
                json: function () { return Promise.resolve({ existe: false }); },
            });
        },
    });
    carregar(sandbox, [
        'static/js/pocket-precadastro-posicao.js',
        'static/js/pocket-cadastro.js',
    ]);
    pronto(doc, sandbox);

    codigo.value = 'ERRADA';
    bip(codigo, '00123', { selecionar: true, selecaoEstagnada: true });
    assert(codigo.value === '00123', 'posição: seleção estagnada preserva 00123, veio ' + codigo.value);
    assert(doc.activeElement === codigo, 'posição: bip sem Enter permanece no código');
    assert(envios === 0, 'posição: bip não envia o formulário');

    codigo.value = '';
    codigo.setSelectionRange(0, 0);
    bip(codigo, '07890');
    assert(codigo.value === '07890', 'posição: campo vazio preserva zeros, veio ' + codigo.value);

    const antes = doc.activeElement;
    codigo.dispatchEvent(evento('keydown', { key: 'Enter' }));
    assert(doc.activeElement === antes, 'posição: keydown Enter ainda não avança');
    assert(envios === 0, 'posição: keydown Enter não salva');
    codigo.dispatchEvent(evento('keyup', { key: 'Enter' }));
    return drenar().then(function () {
        assert(consultas === 1, 'posição: Enter valida uma vez, consultas=' + consultas);
        assert(doc.activeElement === posicao, 'posição: keyup Enter foca a posição');
        assert(envios === 0, 'posição: Enter não salva');
        assert(posicao.disabled === false, 'posição: código novo libera o campo posição');

        const skuForm = doc.criar('form', { id: 'pocket-precadastro-produto-form', class: 'pocket-cadastro-form' });
        skuForm.className = 'pocket-cadastro-form';
        const sku = doc.criar('input', { id: 'id_codigo_produto', name: 'codigo_produto', maxlength: '50' });
        const descricao = doc.criar('input', { id: 'id_descricao', name: 'descricao', maxlength: '255' });
        const embalagem = doc.criar('select', { id: 'id_embalagem', name: 'embalagem' });
        embalagem.tagName = 'SELECT';
        skuForm.appendChild(sku);
        skuForm.appendChild(descricao);
        skuForm.appendChild(embalagem);
        skuForm.appendChild(salvar);
        doc.body.appendChild(skuForm);
        // O cadastro já iniciou no outro form. Reexecuta a instalação no form de produto
        // chamando de novo não ocorre; o teste de produto usa outro documento.
        teclado.dispatchEvent(evento('click', {}));
        assert(codigo.value === '07890', 'teclado não apaga o código, veio ' + codigo.value);
        assert(codigo.getAttribute('inputmode') === 'text', 'teclado virtual liga inputmode text');
        teclado.dispatchEvent(evento('click', {}));
        assert(codigo.getAttribute('inputmode') === 'none', 'teclado físico volta para inputmode none');
        assert(codigo.value === '07890', 'voltar o teclado não apaga o código');
    });
}

function simularProduto() {
    const doc = criarDocumento();
    const teclado = doc.criar('button', { id: 'pocket-cadastro-teclado', type: 'button' });
    const form = doc.criar('form', { id: 'pocket-precadastro-produto-form', class: 'pocket-cadastro-form' });
    form.className = 'pocket-cadastro-form';
    const sku = doc.criar('input', { id: 'id_codigo_produto', name: 'codigo_produto', maxlength: '4' });
    const descricao = doc.criar('input', { id: 'id_descricao', name: 'descricao' });
    const salvar = doc.criar('button', { type: 'submit' });
    salvar.setAttribute('type', 'submit');
    form.appendChild(sku);
    form.appendChild(descricao);
    form.appendChild(salvar);
    doc.body.appendChild(teclado);
    doc.body.appendChild(form);
    let envios = 0;
    form.addEventListener('submit', function () { envios += 1; });
    const sandbox = sandboxDe(doc);
    carregar(sandbox, ['static/js/pocket-cadastro.js']);
    pronto(doc, sandbox);

    sku.value = 'X';
    bip(sku, '00123', { selecionar: true, selecaoEstagnada: true });
    assert(sku.value === '0012', 'SKU respeita maxlength 4, veio ' + sku.value);
    assert(descricao.value === '', 'descrição não recebe o bip do SKU');
    sku.value = '';
    sku.setSelectionRange(0, 0);
    bip(sku, '0099');
    assert(sku.value === '0099', 'SKU vazio preserva zeros, veio ' + sku.value);
    sku.dispatchEvent(evento('keydown', { key: 'Enter' }));
    assert(doc.activeElement === sku, 'produto: keydown Enter não vai para a descrição');
    sku.dispatchEvent(evento('keyup', { key: 'Enter' }));
    assert(doc.activeElement === descricao, 'produto: keyup Enter foca a descrição');
    assert(envios === 0, 'produto: Enter não salva');
    descricao.dispatchEvent(evento('keydown', { key: 'Enter' }));
    descricao.dispatchEvent(evento('keyup', { key: 'Enter' }));
    assert(doc.activeElement === salvar, 'último campo: Enter foca Salvar');
    assert(envios === 0, 'foco em Salvar não confirma o envio');
    assert(form.getAttribute('data-cadastro-enviando') !== '1', 'formulário não foi marcado como enviado');
}

function simularContagem() {
    const doc = criarDocumento();
    const toast = doc.criar('div', { id: 'pocket-toast' });
    const form = doc.criar('form', { id: 'pocket-contagem-form' });
    form.dataset.postUrl = '/pocket/contagem/';
    const posicao = doc.criar('input', { id: 'id_codigo_posicao', name: 'codigo_posicao', maxlength: '50' });
    const produto = doc.criar('input', { id: 'id_codigo_produto', name: 'codigo_produto', maxlength: '50' });
    const quantidade = doc.criar('input', { id: 'id_quantidade_fisica', name: 'quantidade_fisica' });
    const salvar = doc.criar('button', { id: 'pocket-btn-salvar', type: 'submit' });
    const posicaoConfirm = doc.criar('p', { id: 'pocket-posicao-confirm' });
    const posicaoLabel = doc.criar('strong', { id: 'posicao-alocacao' });
    const descricaoConfirm = doc.criar('p', { id: 'pocket-descricao-confirm' });
    const produtoDescricao = doc.criar('strong', { id: 'produto-descricao' });
    form.appendChild(posicao);
    form.appendChild(produto);
    form.appendChild(quantidade);
    form.appendChild(salvar);
    doc.body.appendChild(toast);
    doc.body.appendChild(form);
    doc.body.appendChild(posicaoConfirm);
    doc.body.appendChild(posicaoLabel);
    doc.body.appendChild(descricaoConfirm);
    doc.body.appendChild(produtoDescricao);

    const temporizadores = [];
    let fetches = 0;
    let seqFetch = 0;
    const fila = [];
    const sandbox = sandboxDe(doc, {
        setTimeout: function (fn, ms) {
            const id = temporizadores.length + 1;
            temporizadores.push({ id: id, fn: fn, ms: ms, limpo: false });
            return id;
        },
        clearTimeout: function (id) {
            const item = temporizadores.find(function (t) { return t.id === id; });
            if (item) item.limpo = true;
        },
        fetch: function () {
            fetches += 1;
            const id = ++seqFetch;
            return new Promise(function (resolve) {
                fila.push(function (corpo) {
                    resolve({
                        ok: true,
                        status: 200,
                        type: 'basic',
                        redirected: false,
                        url: '/pocket/contagem/',
                        headers: { get: function () { return 'application/json'; } },
                        json: function () { return Promise.resolve(corpo || { ok: true }); },
                        text: function () { return Promise.resolve(''); },
                    });
                });
                fila[fila.length - 1]._id = id;
            });
        },
        location: { href: 'http://local/pocket/contagem/' },
        FormData: function FormData() {
            this._dados = [];
            this.append = function (chave, valor) { this._dados.push([chave, valor]); };
            this.get = function (chave) {
                const par = this._dados.find(function (item) { return item[0] === chave; });
                return par ? par[1] : null;
            };
        },
    });
    carregar(sandbox, ['static/js/pocket-bipagem.js']);
    sandbox.PocketBipagem.initGeral({
        ajax: true,
        form: form,
        csrfToken: 'csrf',
        postUrl: '/pocket/contagem/',
        inventarioId: '1',
        posicaoInput: posicao,
        produtoInput: produto,
        quantidadeInput: quantidade,
        posicaoConfirm: posicaoConfirm,
        posicaoLabel: posicaoLabel,
        descricaoConfirm: descricaoConfirm,
        produtoDescricao: produtoDescricao,
        btnSalvar: salvar,
        mapaPosicoes: { '00123': 'Rua 1' },
        mapaProdutos: { '00123': 'Produto zero' },
        mapaEan: { '07890': { codigo_produto: '00123', descricao: 'Pelo EAN' } },
    });

    posicao.value = 'VELHA';
    bip(posicao, '00123', { selecionar: true, selecaoEstagnada: true });
    assert(posicao.value === '00123', 'contagem: seleção estagnada preserva 00123, veio ' + posicao.value);
    assert(fetches === 0, 'contagem: bip sem Enter não consulta, fetches=' + fetches);
    const pendentes = temporizadores.filter(function (t) { return !t.limpo && t.ms === 2000; });
    assert(pendentes.length === 1, 'contagem: um timer de 2000 ms fica armado, n=' + pendentes.length);

    posicao.dispatchEvent(evento('keydown', { key: 'Enter' }));
    assert(temporizadores.filter(function (t) { return !t.limpo && t.ms === 2000; }).length === 0,
        'contagem: Enter cancela o timer');
    assert(fetches === 0, 'contagem: keydown Enter ainda não valida');
    posicao.dispatchEvent(evento('keyup', { key: 'Enter' }));
    assert(fetches === 1, 'contagem: keyup Enter valida uma vez, fetches=' + fetches);
    pendentes.forEach(function (t) { if (!t.limpo) t.fn(); });
    assert(fetches === 1, 'contagem: timer cancelado não valida de novo, fetches=' + fetches);

    const trava = fila.shift();
    trava({ ok: true, posicao_codigo: '00123', posicao_alocacao: 'Rua 1' });
    return drenar().then(function () {
        assert(doc.activeElement === produto, 'contagem: posição válida avança para o produto');
        produto.value = '';
        bip(produto, '07890');
        assert(produto.value === '07890', 'contagem: EAN com zero permanece, veio ' + produto.value);
        produto.dispatchEvent(evento('keydown', { key: 'Enter' }));
        produto.dispatchEvent(evento('keyup', { key: 'Enter' }));
        assert(doc.activeElement === quantidade, 'contagem: produto válido avança para quantidade');
        quantidade.value = '2';
        quantidade.dispatchEvent(evento('keydown', { key: 'Enter' }));
        assert(fetches === 1, 'quantidade: keydown Enter não salva');
        quantidade.dispatchEvent(evento('keyup', { key: 'Enter' }));
        assert(fetches === 2, 'quantidade: keyup Enter salva uma vez, fetches=' + fetches);
        quantidade.dispatchEvent(evento('keyup', { key: 'Enter' }));
        assert(fetches === 2, 'quantidade: keyup solto não salva de novo, fetches=' + fetches);

        const vazio = doc.criar('input', { id: 'id_vazio', maxlength: '50' });
        doc.body.appendChild(vazio);
        sandbox.PocketBipagem.initGeral({
            ajax: true,
            form: form,
            csrfToken: 'csrf',
            postUrl: '/pocket/contagem/',
            inventarioId: '1',
            posicaoInput: vazio,
            produtoInput: produto,
            quantidadeInput: quantidade,
            posicaoConfirm: posicaoConfirm,
            posicaoLabel: posicaoLabel,
            descricaoConfirm: descricaoConfirm,
            produtoDescricao: produtoDescricao,
            btnSalvar: salvar,
            mapaPosicoes: {},
            mapaProdutos: {},
            mapaEan: {},
        });
        const antes = fetches;
        vazio.value = '';
        vazio.dispatchEvent(evento('keydown', { key: 'Enter' }));
        vazio.dispatchEvent(evento('keyup', { key: 'Enter' }));
        assert(fetches === antes, 'contagem: campo vazio não valida');
    });
}

Promise.resolve()
    .then(simularCadastro)
    .then(simularProduto)
    .then(simularContagem)
    .then(function () {
        if (falhas.length) {
            falhas.forEach(function (msg) { console.error('FALHA: ' + msg); });
            process.exit(1);
        }
        console.log('Simulação de wedge: ok (não é bip físico).');
        process.exit(0);
    })
    .catch(function (erro) {
        console.error(erro && erro.stack ? erro.stack : erro);
        process.exit(1);
    });
