/**
 * Executa o JS real do Pocket contra um DOM no formato dos templates de contagem.
 * Não é leitura física de coletor e não prova o aparelho. Não grava código nenhum.
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
        el.insertAdjacentElement = function (onde, novo) {
            if (onde === 'beforeend') return el.appendChild(novo);
            if (onde === 'afterbegin') {
                novo.parentNode = el;
                el.children.unshift(novo);
                return novo;
            }
            const pai = el.parentNode;
            if (!pai) return null;
            const idx = pai.children.indexOf(el);
            novo.parentNode = pai;
            if (onde === 'afterend') pai.children.splice(Math.max(idx, 0) + 1, 0, novo);
            else pai.children.splice(Math.max(idx, 0), 0, novo);
            return novo;
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
        assert(codigo.getAttribute('inputmode') === null, 'teclado físico tira inputmode para o wedge gravar');
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

function ativos(lista, ms) {
    return lista.filter(function (t) {
        return !t.limpo && t.ms === ms;
    });
}

function dispararMs(lista, ms) {
    ativos(lista, ms).forEach(function (t) {
        if (t.limpo) return;
        t.limpo = true;
        t.fn();
    });
}

function ler(relativo) {
    return fs.readFileSync(path.join(raiz, relativo), 'utf8');
}

function assertTemplatesDaContagem() {
    const geral = ler('inventario/templates/inventario/pocket/contagem.html');
    const ciclico = ler('inventario/templates/inventario/pocket/contagem_ciclico.html');
    const forms = ler('inventario/forms.py');
    assert(geral.includes('pocket-bipagem.js'), 'contagem carrega pocket-bipagem.js');
    assert(geral.includes('PocketBipagem.initGeral'), 'contagem chama initGeral');
    assert(geral.includes('id="pocket-contagem-form"'), 'contagem tem o form real');
    assert(geral.includes('form.codigo_posicao.id_for_label'), 'posição usa o id do form');
    assert(!geral.includes('pocket-cadastro.js'), 'contagem não carrega o script de cadastro');
    assert(ciclico.includes('pocket-bipagem.js'), 'cíclico carrega pocket-bipagem.js');
    assert(ciclico.includes('PocketBipagem.initCiclico'), 'cíclico chama initCiclico');
    assert(ciclico.includes('pocket-produto-ciclico'), 'cíclico usa o id real do produto');
    assert(!ciclico.includes('pocket-cadastro.js'), 'cíclico não carrega o script de cadastro');
    assert(forms.includes("'id': 'pocket-produto-ciclico'"), 'form cíclico fixa o id do produto');
    assert(!forms.includes("inputmode': 'none'"), 'forms não usam inputmode none');
    assert(!geral.includes('virtualkeyboardpolicy'), 'template geral sem virtualkeyboardpolicy');
    assert(!ciclico.includes('virtualkeyboardpolicy'), 'template cíclico sem virtualkeyboardpolicy');
}

function campoDaContagem(doc, id, name, extras) {
    const secao = doc.criar('section', { class: 'pocket-scan-sec' });
    const wrap = doc.criar('div', { class: 'pocket-scan-input-wrap' });
    const input = doc.criar('input', {
        id: id,
        name: name,
        type: 'text',
        maxlength: '50',
    });
    Object.keys(extras || {}).forEach(function (chave) {
        input.setAttribute(chave, extras[chave]);
    });
    wrap.appendChild(input);
    secao.appendChild(wrap);
    return { secao: secao, input: input };
}

function sandboxContagem(doc) {
    const temporizadores = [];
    const fila = [];
    let fetches = 0;
    let alertas = 0;
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
            });
        },
        alert: function () { alertas += 1; },
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
    return {
        sandbox: sandbox,
        temporizadores: temporizadores,
        fila: fila,
        alertas: function () { return alertas; },
        fetches: function () { return fetches; },
    };
}

function montarContagemGeral(doc) {
    const toast = doc.criar('div', { id: 'pocket-toast' });
    const form = doc.criar('form', { id: 'pocket-contagem-form', class: 'pocket-scan-form' });
    form.className = 'pocket-scan-form';
    form.setAttribute('data-post-url', '/pocket/contagem/1/');
    form.setAttribute('data-inventario-id', '1');
    form.dataset.postUrl = '/pocket/contagem/1/';
    form.dataset.inventarioId = '1';
    const posicao = campoDaContagem(doc, 'id_codigo_posicao', 'codigo_posicao', { autofocus: 'true' });
    const produto = campoDaContagem(doc, 'id_codigo_produto', 'codigo_produto');
    const quantidade = campoDaContagem(doc, 'id_quantidade_fisica', 'quantidade_fisica', { inputmode: 'numeric' });
    const salvar = doc.criar('button', { id: 'pocket-btn-salvar', type: 'submit' });
    salvar.setAttribute('type', 'submit');
    const posicaoConfirm = doc.criar('p', { id: 'pocket-posicao-confirm' });
    const posicaoLabel = doc.criar('strong', { id: 'posicao-alocacao' });
    const descricaoConfirm = doc.criar('p', { id: 'pocket-descricao-confirm' });
    const produtoDescricao = doc.criar('strong', { id: 'produto-descricao' });
    form.appendChild(posicao.secao);
    form.appendChild(produto.secao);
    form.appendChild(quantidade.secao);
    form.appendChild(salvar);
    doc.body.appendChild(toast);
    doc.body.appendChild(form);
    doc.body.appendChild(posicaoConfirm);
    doc.body.appendChild(posicaoLabel);
    doc.body.appendChild(descricaoConfirm);
    doc.body.appendChild(produtoDescricao);
    return {
        form: form,
        posicao: posicao.input,
        produto: produto.input,
        quantidade: quantidade.input,
        salvar: salvar,
        posicaoConfirm: posicaoConfirm,
        posicaoLabel: posicaoLabel,
        descricaoConfirm: descricaoConfirm,
        produtoDescricao: produtoDescricao,
    };
}

function opcoesGerais(ui) {
    return {
        ajax: true,
        form: ui.form,
        csrfToken: 'csrf',
        postUrl: '/pocket/contagem/1/',
        inventarioId: '1',
        posicaoInput: ui.posicao,
        produtoInput: ui.produto,
        quantidadeInput: ui.quantidade,
        posicaoConfirm: ui.posicaoConfirm,
        posicaoLabel: ui.posicaoLabel,
        descricaoConfirm: ui.descricaoConfirm,
        produtoDescricao: ui.produtoDescricao,
        btnSalvar: ui.salvar,
        mapaPosicoes: { '00123': 'Rua 1' },
        mapaProdutos: { '00123': 'Produto zero' },
        mapaEan: { '07890': { codigo_produto: '00123', descricao: 'Pelo EAN' } },
    };
}

function simularCadastroSemAtraso() {
    const doc = criarDocumento();
    const teclado = doc.criar('button', { id: 'pocket-cadastro-teclado', type: 'button' });
    const form = doc.criar('form', { id: 'pocket-precadastro-posicao-form', class: 'pocket-cadastro-form' });
    form.className = 'pocket-cadastro-form';
    form.dataset.validarCodigoUrl = '/validar';
    form.dataset.sucesso = '0';
    const codigo = doc.criar('input', { id: 'id_codigo', name: 'codigo', maxlength: '50' });
    codigo.setAttribute('data-cadastro-enter', 'validar-codigo');
    const posicao = doc.criar('input', { id: 'id_posicao', name: 'posicao' });
    form.appendChild(codigo);
    form.appendChild(posicao);
    doc.body.appendChild(teclado);
    doc.body.appendChild(form);
    const temporizadores = [];
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
    assert(!sandbox.PocketBipagem, 'cadastro não recebe PocketBipagem');
    assert(!ler('static/js/pocket-cadastro.js').includes('POCKET_VALIDACAO_ATRASO_MS'), 'cadastro.js sem timer da contagem');
    assert(!ler('static/js/pocket-precadastro-posicao.js').includes('POCKET_VALIDACAO_ATRASO_MS'), 'precadastro sem timer da contagem');
    pronto(doc, sandbox);
    codigo.value = '00123';
    codigo.dispatchEvent(evento('input', {}));
    codigo.dispatchEvent(evento('keydown', { key: 'Enter' }));
    codigo.dispatchEvent(evento('keyup', { key: 'Enter' }));
    assert(ativos(temporizadores, 2000).length === 0, 'cadastro não arma pausa de 2000 ms');
}

function simularContagem() {
    assertTemplatesDaContagem();
    const doc = criarDocumento();
    const ui = montarContagemGeral(doc);
    const ambiente = sandboxContagem(doc);
    const sandbox = ambiente.sandbox;
    let erroInit = null;
    try {
        sandbox.PocketBipagem.initGeral(opcoesGerais(ui));
    } catch (erro) {
        erroInit = erro;
    }
    assert(!erroInit, 'initGeral lançou ' + (erroInit && erroInit.message));
    assert(sandbox.PocketBipagem.POCKET_VALIDACAO_ATRASO_MS === 2000, 'atraso real é 2000');
    assert(ui.posicao.getAttribute('inputmode') === null, 'posição segue sem inputmode');
    assert(ui.produto.getAttribute('inputmode') === null, 'produto segue sem inputmode');
    assert(ui.quantidade.getAttribute('inputmode') === 'numeric', 'quantidade permanece numeric');
    assert(ui.posicao.getAttribute('virtualkeyboardpolicy') === null, 'init não aplica virtualkeyboardpolicy');

    ui.posicao.dispatchEvent(evento('keydown', { key: 'Enter' }));
    dispararMs(ambiente.temporizadores, 0);
    assert(ambiente.fetches() === 0, 'campo vazio não valida');

    ui.posicao.value = 'XXXX';
    ui.posicao.dispatchEvent(evento('input', {}));
    ui.posicao.dispatchEvent(evento('keydown', { key: 'Enter' }));
    dispararMs(ambiente.temporizadores, 0);
    return drenar().then(function () {
        assert(doc.activeElement === ui.posicao, 'código inválido permanece na posição');
        assert(doc.querySelectorAll('.pocket-campo-erro').length === 1, 'erro inline, sem alerta bloqueante');
        assert(ambiente.alertas() === 0, 'não usa alert');
        assert(ambiente.fetches() === 0, 'inválido local não consulta');
        ui.posicao.dispatchEvent(evento('keydown', { key: 'Enter' }));
        ui.posicao.dispatchEvent(evento('keyup', { key: 'Enter' }));
        dispararMs(ambiente.temporizadores, 0);
        return drenar();
    }).then(function () {
        assert(doc.querySelectorAll('.pocket-campo-erro').length === 1, 'não repete o erro do mesmo valor');
        assert(doc.activeElement === ui.posicao, 'inválido repetido não avança');

        ui.posicao.value = 'VELHA';
        ui.posicao.focus();
        ui.posicao.select();
        const antesTimers = ambiente.temporizadores.filter(function (t) { return t.ms === 2000; }).length;
        bip(ui.posicao, '00123', { selecionar: true, selecaoEstagnada: true });
        assert(ui.posicao.value === '00123', 'seleção estagnada preserva 00123, veio ' + ui.posicao.value);
        const novos = ambiente.temporizadores.filter(function (t) { return t.ms === 2000; }).slice(antesTimers);
        assert(novos.length > 1, 'cada caractere rearma a pausa');
        assert(ativos(ambiente.temporizadores, 2000).length === 1, 'só o último timer de 2000 ms fica armado');
        assert(ambiente.fetches() === 0, 'bip sem Enter não consulta');

        dispararMs(ambiente.temporizadores, 2000);
        assert(ambiente.fetches() === 1, 'pausa de 2000 ms valida a posição');
        dispararMs(ambiente.temporizadores, 2000);
        assert(ambiente.fetches() === 1, 'pausa não valida de novo');
        const trava = ambiente.fila.shift();
        trava({ ok: true, posicao_codigo: '00123', posicao_alocacao: 'Rua 1' });
        return drenar();
    }).then(function () {
        assert(doc.activeElement === ui.produto, 'posição válida avança para o produto');
        bip(ui.produto, '07890');
        assert(ui.produto.value === '07890', 'EAN com zero permanece, veio ' + ui.produto.value);
        const pausaProduto = ativos(ambiente.temporizadores, 2000);
        assert(pausaProduto.length === 1, 'produto arma uma pausa');
        ui.produto.dispatchEvent(evento('keydown', { key: 'Enter' }));
        assert(pausaProduto[0].limpo === false, 'keydown do Enter ainda não cancela a pausa');
        assert(ambiente.fetches() === 1, 'keydown do Enter ainda não precisa de outra consulta');
        ui.produto.dispatchEvent(evento('keyup', { key: 'Enter' }));
        assert(doc.activeElement === ui.quantidade, 'keyup do Enter avança para a quantidade');
        assert(pausaProduto[0].limpo === true, 'validar no keyup cancela a pausa');
        assert(ambiente.fetches() === 1, 'produto do mapa não consulta de novo');
        dispararMs(ambiente.temporizadores, 0);
        assert(doc.activeElement === ui.quantidade, 'fallback do Enter não avança de novo');
        assert(ambiente.fetches() === 1, 'fallback não valida em duplicata');

        ui.quantidade.value = '2';
        ui.quantidade.dispatchEvent(evento('input', {}));
        dispararMs(ambiente.temporizadores, 2000);
        assert(ambiente.fetches() === 1, 'pausa na quantidade não salva');
        ui.quantidade.dispatchEvent(evento('keydown', { key: 'Enter' }));
        assert(ambiente.fetches() === 1, 'keydown da quantidade ainda não salva');
        ui.quantidade.dispatchEvent(evento('keyup', { key: 'Enter' }));
        assert(ambiente.fetches() === 2, 'keyup da quantidade salva uma vez');
        dispararMs(ambiente.temporizadores, 0);
        ui.quantidade.dispatchEvent(evento('keyup', { key: 'Enter' }));
        assert(ambiente.fetches() === 2, 'Enter repetido não grava de novo');
    });
}

function simularEnterSemKeyup() {
    const doc = criarDocumento();
    const ui = montarContagemGeral(doc);
    const ambiente = sandboxContagem(doc);
    ambiente.sandbox.PocketBipagem.initGeral(opcoesGerais(ui));
    bip(ui.posicao, '00123');
    const pausa = ativos(ambiente.temporizadores, 2000);
    assert(pausa.length === 1, 'sem keyup: a pausa fica armada depois do bip');
    ui.posicao.dispatchEvent(evento('keydown', { key: 'Enter' }));
    assert(pausa[0].limpo === false, 'sem keyup: o keydown não desarma a pausa');
    assert(ambiente.fetches() === 0, 'sem keyup: o keydown ainda não valida');
    dispararMs(ambiente.temporizadores, 0);
    assert(ambiente.fetches() === 1, 'sem keyup: o turno seguinte valida');
    assert(pausa[0].limpo === true, 'sem keyup: validar cancela a pausa');
    dispararMs(ambiente.temporizadores, 2000);
    assert(ambiente.fetches() === 1, 'sem keyup: a pausa cancelada não valida outra vez');
    const trava = ambiente.fila.shift();
    trava({ ok: true, posicao_codigo: '00123', posicao_alocacao: 'Rua 1' });
    return drenar().then(function () {
        assert(doc.activeElement === ui.produto, 'sem keyup: posição válida avança');
    });
}

function simularRespostaTardia() {
    const doc = criarDocumento();
    const ui = montarContagemGeral(doc);
    const ambiente = sandboxContagem(doc);
    ambiente.sandbox.PocketBipagem.initGeral(opcoesGerais(ui));
    bip(ui.posicao, '00123');
    dispararMs(ambiente.temporizadores, 2000);
    ui.posicao.value = '00LATE';
    const trava = ambiente.fila.shift();
    trava({ ok: true, posicao_codigo: '00123', posicao_alocacao: 'Rua 1' });
    return drenar().then(function () {
        assert(doc.activeElement !== ui.produto, 'resposta tardia não foca o produto');
        assert(ui.posicao.value === '00LATE', 'resposta tardia não apaga a correção');
        const liberar = ambiente.fila.shift();
        if (liberar) liberar({ ok: true });
        return drenar();
    }).then(function () {
        ui.posicao.value = '00123';
        ui.posicao.dispatchEvent(evento('input', {}));
        dispararMs(ambiente.temporizadores, 2000);
        ui.quantidade.focus();
        const travaFoco = ambiente.fila.shift();
        travaFoco({ ok: true, posicao_codigo: '00123', posicao_alocacao: 'Rua 1' });
        return drenar();
    }).then(function () {
        assert(doc.activeElement === ui.quantidade, 'não puxa o foco de quem está noutro campo');
    });
}

function simularCiclico() {
    const doc = criarDocumento();
    const toast = doc.criar('div', { id: 'pocket-toast' });
    const form = doc.criar('form', { id: 'pocket-contagem-form', class: 'pocket-scan-form' });
    form.className = 'pocket-scan-form';
    const posicao = campoDaContagem(doc, 'id_codigo_posicao', 'codigo_posicao', { autofocus: 'true' });
    const produto = campoDaContagem(doc, 'pocket-produto-ciclico', 'codigo_produto_lido');
    const quantidade = campoDaContagem(doc, 'id_quantidade_fisica', 'quantidade_fisica', { inputmode: 'numeric' });
    const sku = doc.criar('select', { id: 'pocket-sku-lote', name: 'sku_id' });
    sku.tagName = 'SELECT';
    const salvar = doc.criar('button', { id: 'pocket-btn-salvar', type: 'submit' });
    salvar.setAttribute('type', 'submit');
    form.appendChild(sku);
    form.appendChild(posicao.secao);
    form.appendChild(produto.secao);
    form.appendChild(quantidade.secao);
    form.appendChild(salvar);
    doc.body.appendChild(toast);
    doc.body.appendChild(form);
    const ambiente = sandboxContagem(doc);
    let erroInit = null;
    try {
        ambiente.sandbox.PocketBipagem.initCiclico({
            form: form,
            csrfToken: 'csrf',
            posicaoInput: posicao.input,
            produtoInput: produto.input,
            quantidadeInput: quantidade.input,
            skuSelect: sku,
            btnSalvar: salvar,
            mapaPosicoes: { '00123': 'Rua 1' },
            mapaProdutos: { '00123': 'Produto zero' },
            mapaEan: { '07890': { codigo_produto: '00123', descricao: 'Pelo EAN' } },
            mapaSkus: {
                '9': { codigo_produto: '00123', codigo_ean: '07890' },
            },
        });
        sku.value = '9';
    } catch (erro) {
        erroInit = erro;
    }
    assert(!erroInit, 'initCiclico lançou ' + (erroInit && erroInit.message));
    bip(posicao.input, '00123');
    assert(ativos(ambiente.temporizadores, 2000).length === 1, 'cíclico arma a pausa de 2000 ms');
    dispararMs(ambiente.temporizadores, 2000);
    assert(ambiente.fetches() === 1, 'cíclico: a pausa valida a posição');

    const docEnter = criarDocumento();
    const toastEnter = docEnter.criar('div', { id: 'pocket-toast' });
    const formEnter = docEnter.criar('form', { id: 'pocket-contagem-form' });
    const posicaoEnter = campoDaContagem(docEnter, 'id_codigo_posicao', 'codigo_posicao');
    const produtoEnter = campoDaContagem(docEnter, 'pocket-produto-ciclico', 'codigo_produto_lido');
    const quantidadeEnter = campoDaContagem(docEnter, 'id_quantidade_fisica', 'quantidade_fisica');
    formEnter.appendChild(posicaoEnter.secao);
    formEnter.appendChild(produtoEnter.secao);
    formEnter.appendChild(quantidadeEnter.secao);
    docEnter.body.appendChild(toastEnter);
    docEnter.body.appendChild(formEnter);
    const enter = sandboxContagem(docEnter);
    enter.sandbox.PocketBipagem.initCiclico({
        form: formEnter,
        csrfToken: 'csrf',
        posicaoInput: posicaoEnter.input,
        produtoInput: produtoEnter.input,
        quantidadeInput: quantidadeEnter.input,
        btnSalvar: docEnter.criar('button', { id: 'pocket-btn-salvar', type: 'submit' }),
        mapaPosicoes: { '00123': 'Rua 1' },
        mapaProdutos: {},
        mapaEan: {},
        mapaSkus: {},
    });
    bip(posicaoEnter.input, '00123');
    const pausa = ativos(enter.temporizadores, 2000);
    posicaoEnter.input.dispatchEvent(evento('keydown', { key: 'Enter' }));
    assert(pausa[0].limpo === false, 'cíclico: keydown não cancela a pausa');
    assert(enter.fetches() === 0, 'cíclico: keydown ainda não valida');
    dispararMs(enter.temporizadores, 0);
    assert(enter.fetches() === 1, 'cíclico: Enter sem keyup valida');
    assert(pausa[0].limpo === true, 'cíclico: validar cancela a pausa');
    quantidadeEnter.input.value = '4';
    quantidadeEnter.input.focus();
    quantidadeEnter.input.dispatchEvent(evento('input', {}));
    const fetchesQuantidade = enter.fetches();
    dispararMs(enter.temporizadores, 2000);
    assert(enter.fetches() === fetchesQuantidade, 'cíclico: pausa na quantidade não salva');
}

Promise.resolve()
    .then(simularCadastro)
    .then(simularProduto)
    .then(simularCadastroSemAtraso)
    .then(simularContagem)
    .then(simularEnterSemKeyup)
    .then(simularRespostaTardia)
    .then(simularCiclico)
    .then(function () {
        if (falhas.length) {
            falhas.forEach(function (msg) { console.error('FALHA: ' + msg); });
            process.exit(1);
        }
        console.log('Simulação do JS real: ok. Node não prova o coletor físico.');
        process.exit(0);
    })
    .catch(function (erro) {
        console.error(erro && erro.stack ? erro.stack : erro);
        process.exit(1);
    });
