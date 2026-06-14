(function () {
    'use strict';

    var chartRegistry = {};
    var payloadCache = null;
    var datalabelsRegistrado = false;

    var CORES = {
        azul: '#2563EB',
        azulEscuro: '#1E40AF',
        azulClaro: '#60A5FA',
        verde: '#16A34A',
        laranja: '#F97316',
        vermelho: '#DC2626',
        cinza: '#64748B',
        cinzaClaro: '#E2E8F0',
        texto: '#334155',
        azulSuave: 'rgba(37, 99, 235, 0.1)',
    };

    var PALETA_EMBALAGENS = [
        CORES.azulEscuro,
        CORES.azul,
        CORES.azulClaro,
        CORES.cinza,
        CORES.azul,
    ];

    var MENSAGEM_SEM_DADOS = 'Sem dados para exibição';

    function totalDataset(dataset) {
        if (!dataset || !dataset.data) {
            return 0;
        }
        return dataset.data.reduce(function (acc, val) {
            return acc + Number(val || 0);
        }, 0);
    }

    function rotuloCompleto(chart, indice) {
        if (!chart || !chart.data) {
            return '—';
        }
        var completos = chart.data.labelsCompletos || chart.data.labels || [];
        return String(completos[indice] || chart.data.labels[indice] || '—');
    }

    function escapeHtml(texto) {
        return String(texto || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function formatarNomeExibicao(texto, graficoId) {
        var valor = String(texto || '—').trim();
        if (valor === '—' || valor === '') {
            return 'Sem dados';
        }
        if (graficoId === 'ranking_usuarios') {
            return formatarNomeRanking(valor);
        }
        if (valor.length <= 14) {
            return valor;
        }
        var partes = valor.split(/\s+/);
        if (partes.length >= 2 && partes[0].length <= 12) {
            return partes[0] + '\n' + partes.slice(1).join(' ');
        }
        return valor.slice(0, 11) + '…';
    }

    function formatarNomeRanking(texto) {
        if (texto.length <= 16) {
            return texto;
        }
        return texto.slice(0, 14) + '…';
    }

    function formatarNomeEixo(texto, graficoId) {
        if (graficoId === 'ranking_usuarios') {
            return formatarNomeRanking(String(texto || '—'));
        }
        return formatarNomeExibicao(texto).toUpperCase();
    }

    function calcularGrace(valores) {
        var max = Math.max.apply(null, (valores || []).concat([0]));
        if (max > 200) {
            return '24%';
        }
        if (max > 50) {
            return '20%';
        }
        return '16%';
    }

    function calcularPaddingTopBar(valores) {
        var max = Math.max.apply(null, (valores || []).concat([0]));
        if (max > 200) {
            return 29;
        }
        if (max > 50) {
            return 24;
        }
        return 19;
    }

    function percentualValor(valor, total) {
        if (!total) {
            return 0;
        }
        return Math.round((Number(valor) / total) * 100);
    }

    function formatLabelBar(value) {
        return String(Number(value || 0));
    }

    function obterConfigBarras(graficoId) {
        if (graficoId === 'ranking_usuarios') {
            return {
                categoryPercentage: 0.5,
                barPercentage: 0.4,
                maxBarThickness: 26,
            };
        }
        return {
            categoryPercentage: 0.55,
            barPercentage: 0.5,
            maxBarThickness: 30,
        };
    }

    function corPorRotulo(rotulo) {
        var chave = String(rotulo || '').toLowerCase();
        if (chave.indexOf('finaliz') >= 0 || chave.indexOf('encerr') >= 0 || chave.indexOf('valid') >= 0 || chave.indexOf('corret') >= 0 || chave.indexOf('concili') >= 0) {
            return CORES.verde;
        }
        if (chave.indexOf('ativo') >= 0 || chave.indexOf('andamento') >= 0 || chave.indexOf('contad') >= 0) {
            return CORES.azul;
        }
        if (chave.indexOf('diverg') >= 0 || chave.indexOf('abaixo') >= 0) {
            return CORES.vermelho;
        }
        if (chave.indexOf('acima') >= 0 || chave.indexOf('recont') >= 0 || chave.indexOf('reconf') >= 0 || chave.indexOf('alerta') >= 0) {
            return CORES.laranja;
        }
        if (chave.indexOf('pendent') >= 0 || chave.indexOf('abert') >= 0 || chave.indexOf('planej') >= 0 || chave.indexOf('cancel') >= 0 || chave.indexOf('arquiv') >= 0) {
            return CORES.cinza;
        }
        if (chave.indexOf('cosan') >= 0) {
            return CORES.azul;
        }
        if (chave.indexOf('brida') >= 0) {
            return CORES.azulEscuro;
        }
        return CORES.azul;
    }

    function resolverCores(grafico, labels) {
        var id = grafico.id;

        if (id === 'embalagens') {
            return labels.map(function (_item, indice) {
                return PALETA_EMBALAGENS[indice % PALETA_EMBALAGENS.length];
            });
        }
        if (id === 'canais') {
            return [CORES.azul, CORES.azulEscuro];
        }
        if (id === 'divergencias') {
            return [CORES.cinza, CORES.azul, CORES.vermelho, CORES.verde];
        }
        if (id === 'acuracidade_ciclico') {
            return [CORES.verde, CORES.laranja, CORES.vermelho];
        }
        if (id === 'status_ciclos') {
            return labels.map(corPorRotulo);
        }
        if (id === 'status_inventarios') {
            return labels.map(corPorRotulo);
        }
        if (id === 'planejado_contado') {
            return labels.map(corPorRotulo);
        }
        if (id === 'ranking_usuarios') {
            return labels.map(function () {
                return CORES.azul;
            });
        }
        return labels.map(corPorRotulo);
    }

    function renderLegendaRosca(graficoId, chart) {
        var container = document.querySelector('[data-doughnut-legend="' + graficoId + '"]');
        if (!container || !chart) {
            return;
        }

        var dataset = chart.data.datasets[0];
        if (!dataset) {
            container.innerHTML = '';
            return;
        }

        var total = totalDataset(dataset);
        var itens = chart.data.labelsCompletos || chart.data.labels || [];
        container.innerHTML = '';

        itens.forEach(function (nome, indice) {
            var valor = dataset._semDados ? 0 : Number(dataset.data[indice] || 0);
            var pct = percentualValor(valor, total);
            var coresGrafico = chart.data.chartColors || dataset.backgroundColor;
            var cor = Array.isArray(coresGrafico)
                ? coresGrafico[indice]
                : coresGrafico;

            var item = document.createElement('div');
            item.className = 'dashboard-doughnut-legend-item';
            item.innerHTML =
                '<span class="dashboard-doughnut-legend-dot" style="background-color:' + cor + '"></span>' +
                '<span class="dashboard-doughnut-legend-name" title="' + escapeHtml(nome) + '">' + escapeHtml(nome) + '</span>' +
                '<span class="dashboard-doughnut-legend-value">' + valor + ' (' + pct + '%)</span>';
            container.appendChild(item);
        });
    }

    function validarGraficoPayload(grafico) {
        if (!grafico || typeof grafico !== 'object') {
            return { valido: false, motivo: 'empty' };
        }
        if (!grafico.tipo) {
            return { valido: false, motivo: 'empty' };
        }

        var labels = Array.isArray(grafico.labels) ? grafico.labels : [];
        var valores = Array.isArray(grafico.valores) ? grafico.valores : [];

        if (!labels.length && !valores.length) {
            return { valido: false, motivo: 'empty' };
        }

        return { valido: true, labels: labels, valores: valores };
    }

    function buildOptions(tipo, valores, graficoId) {
        var base = {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 350 },
            layout: {
                padding: {
                    top: 13,
                    bottom: 10,
                    left: 10,
                    right: 13,
                },
            },
            plugins: {
                legend: {
                    display: false,
                },
                tooltip: {
                    enabled: true,
                    backgroundColor: '#1E293B',
                    titleFont: { size: 11 },
                    bodyFont: { size: 11 },
                    padding: 10,
                    callbacks: {
                        title: function (items) {
                            if (!items.length) {
                                return '';
                            }
                            return rotuloCompleto(items[0].chart, items[0].dataIndex);
                        },
                        label: function (ctx) {
                            var val = ctx.parsed.y !== undefined ? ctx.parsed.y : ctx.parsed;
                            var total = totalDataset(ctx.dataset);
                            var pct = percentualValor(val, total);
                            return val + ' (' + pct + '%)';
                        },
                    },
                },
                datalabels: {
                    display: false,
                },
            },
        };

        if (tipo === 'doughnut') {
            base.cutout = '68%';
            base.layout.padding = { top: 6, bottom: 6, left: 3, right: 6 };
            base.plugins.datalabels = { display: false };
            return base;
        }

        var escalaX = {
            grid: { display: false },
            border: { display: false },
            ticks: {
                font: { size: 9, lineHeight: 1.2 },
                maxRotation: 0,
                minRotation: 0,
                autoSkip: false,
                color: CORES.cinza,
                padding: 5,
                callback: function (_valor, indice) {
                    var chart = this.chart;
                    return formatarNomeEixo(rotuloCompleto(chart, indice), graficoId);
                },
            },
        };

        var escalaY = {
            beginAtZero: true,
            grace: calcularGrace(valores),
            grid: {
                color: '#E2E8F0',
            },
            border: { display: false },
            ticks: {
                precision: 0,
                font: { size: 9 },
                color: CORES.cinza,
                maxTicksLimit: 5,
            },
        };

        base.layout.padding.top = calcularPaddingTopBar(valores);
        base.layout.padding.bottom = 6;
        base.plugins.datalabels = {
            display: datalabelsRegistrado,
            color: CORES.texto,
            font: { size: 9, weight: 'bold' },
            anchor: 'end',
            align: 'end',
            offset: 4,
            clip: true,
            clamp: true,
            formatter: formatLabelBar,
        };

        if (tipo === 'line') {
            base.scales = { x: escalaX, y: escalaY };
            base.elements = {
                line: { tension: 0.3, borderWidth: 2 },
                point: { radius: 3, hoverRadius: 4 },
            };
            return base;
        }

        var configBarras = obterConfigBarras(graficoId);
        base.scales = { x: escalaX, y: escalaY };
        base.datasets = {
            bar: {
                categoryPercentage: configBarras.categoryPercentage,
                barPercentage: configBarras.barPercentage,
            },
        };
        return base;
    }

    function normalizeGrafico(grafico) {
        var validacao = validarGraficoPayload(grafico);
        if (!validacao.valido) {
            return { semDados: true, invalido: true, id: grafico.id };
        }

        var labelsCompletos = validacao.labels.map(function (rotulo) {
            return String(rotulo || '—').trim() || 'Sem dados';
        });
        var valores = validacao.valores.map(function (valor) {
            return Number(valor) || 0;
        });
        var semDados = false;

        if (!labelsCompletos.length) {
            labelsCompletos = ['Sem dados'];
            valores = [0];
            semDados = true;
        }

        while (valores.length < labelsCompletos.length) {
            valores.push(0);
        }
        while (labelsCompletos.length < valores.length) {
            labelsCompletos.push('Sem dados');
        }

        var soma = valores.reduce(function (acc, valor) {
            return acc + valor;
        }, 0);

        if (soma === 0 && grafico.tipo === 'doughnut') {
            semDados = true;
            valores = labelsCompletos.map(function () {
                return 1;
            });
        }

        if (soma === 0 && grafico.tipo !== 'doughnut') {
            semDados = true;
        }

        return {
            id: grafico.id,
            titulo: grafico.titulo,
            tipo: grafico.tipo,
            labels: labelsCompletos.map(function (rotulo) {
                return formatarNomeExibicao(rotulo, grafico.id);
            }),
            labelsCompletos: labelsCompletos,
            valores: valores,
            semDados: semDados,
            invalido: false,
        };
    }

    function setChartState(graficoId, state, message) {
        var wrapper = document.querySelector('[data-chart-wrapper="' + graficoId + '"]');
        if (!wrapper) {
            return;
        }
        var loading = wrapper.querySelector('.dashboard-chart-loading');
        var empty = wrapper.querySelector('.dashboard-chart-empty');
        var error = wrapper.querySelector('.dashboard-chart-error');
        var canvas = wrapper.querySelector('canvas');
        var legend = wrapper.querySelector('[data-doughnut-legend]');

        if (loading) {
            loading.classList.toggle('d-none', state !== 'loading');
        }
        if (empty) {
            empty.classList.toggle('d-none', state !== 'empty');
            if (message) {
                empty.textContent = message;
            }
        }
        if (error) {
            error.classList.toggle('d-none', state !== 'error');
            if (message) {
                error.textContent = message;
            }
        }
        if (canvas) {
            canvas.classList.toggle('d-none', state === 'empty' || state === 'error');
        }
        if (legend) {
            legend.classList.toggle('d-none', state === 'empty' || state === 'error');
        }
    }

    function registrarDataLabels() {
        if (datalabelsRegistrado) {
            return;
        }
        if (typeof ChartDataLabels === 'undefined') {
            console.warn('ChartDataLabels não disponível; rótulos de barras desativados.');
            return;
        }
        try {
            Chart.register(ChartDataLabels);
            datalabelsRegistrado = true;
        } catch (err) {
            console.warn('Falha ao registrar ChartDataLabels:', err);
        }
    }

    function createChart(canvas, grafico) {
        var dados = normalizeGrafico(grafico);

        if (dados.invalido) {
            setChartState(grafico.id, 'empty', MENSAGEM_SEM_DADOS);
            return null;
        }

        if (dados.semDados && dados.tipo !== 'doughnut') {
            setChartState(dados.id, 'empty', MENSAGEM_SEM_DADOS);
            return null;
        }

        var cores = resolverCores(grafico, dados.labelsCompletos);
        var configBarras = obterConfigBarras(dados.id);

        if (dados.semDados && dados.tipo === 'doughnut') {
            cores = dados.labelsCompletos.map(function () {
                return CORES.cinzaClaro;
            });
        }

        var dataset = {
            data: dados.valores,
            backgroundColor: dados.tipo === 'line'
                ? CORES.azulSuave
                : cores,
            borderColor: dados.tipo === 'line' ? CORES.azul : undefined,
            borderWidth: dados.tipo === 'line' ? 2 : 0,
            borderRadius: dados.tipo === 'bar' ? 5 : 0,
            maxBarThickness: dados.tipo === 'bar' ? configBarras.maxBarThickness : undefined,
            _semDados: dados.semDados,
        };

        if (dados.tipo === 'line') {
            dataset.fill = true;
            dataset.pointBackgroundColor = CORES.azul;
            dataset.pointBorderColor = '#fff';
            dataset.pointBorderWidth = 1;
        }

        if (dados.tipo === 'doughnut') {
            dataset.borderWidth = 2;
            dataset.borderColor = '#ffffff';
            dataset.hoverBorderColor = '#ffffff';
        }

        if (!dataset.data || !dataset.data.length) {
            setChartState(dados.id, 'empty', MENSAGEM_SEM_DADOS);
            return null;
        }

        if (dataset.data.length !== dados.labels.length) {
            console.error('Gráfico ' + dados.id + ': labels e valores com tamanhos diferentes.');
            setChartState(dados.id, 'error', 'Erro ao carregar gráfico.');
            return null;
        }

        var chartType = dados.tipo === 'line' ? 'line' : (dados.tipo === 'doughnut' ? 'doughnut' : 'bar');

        setChartState(dados.id, 'ready');

        var chart = new Chart(canvas, {
            type: chartType,
            data: {
                labels: dados.labels,
                labelsCompletos: dados.labelsCompletos,
                chartColors: cores,
                datasets: [dataset],
            },
            options: buildOptions(dados.tipo, dados.valores, dados.id),
        });

        if (dados.tipo === 'doughnut') {
            renderLegendaRosca(dados.id, chart);
        }

        return chart;
    }

    function parsePayload() {
        var payloadEl = document.getElementById('dashboard-graficos-data');
        if (!payloadEl) {
            throw new Error('Payload de gráficos não encontrado.');
        }

        var raw = JSON.parse(payloadEl.textContent);
        if (typeof raw === 'string') {
            raw = JSON.parse(raw);
        }

        if (!raw || typeof raw !== 'object') {
            throw new Error('Payload de gráficos inválido.');
        }

        return {
            geral: Array.isArray(raw.geral) ? raw.geral : [],
            ciclico: Array.isArray(raw.ciclico) ? raw.ciclico : [],
        };
    }

    function renderPanelCharts(panelName) {
        if (!payloadCache) {
            return;
        }

        var graficos = payloadCache[panelName] || [];
        graficos.forEach(function (grafico) {
            if (chartRegistry[grafico.id]) {
                chartRegistry[grafico.id].resize();
                if (grafico.tipo === 'doughnut') {
                    renderLegendaRosca(grafico.id, chartRegistry[grafico.id]);
                }
                return;
            }

            var canvas = document.getElementById('chart-' + grafico.id);
            if (!canvas) {
                return;
            }

            var validacao = validarGraficoPayload(grafico);
            if (!validacao.valido) {
                setChartState(grafico.id, 'empty', MENSAGEM_SEM_DADOS);
                return;
            }

            setChartState(grafico.id, 'loading');

            try {
                var chart = createChart(canvas, grafico);
                if (chart) {
                    chartRegistry[grafico.id] = chart;
                }
            } catch (err) {
                console.error('Erro ao renderizar gráfico ' + grafico.id + ':', err);
                setChartState(grafico.id, 'error', 'Erro ao carregar gráfico.');
            }
        });
    }

    function initVisaoGraficos() {
        var select = document.getElementById('visaoGraficos');
        if (!select) {
            return;
        }

        var panels = document.querySelectorAll('[data-dashboard-panel]');

        function aplicar() {
            var visao = select.value;
            panels.forEach(function (panel) {
                var ativo = panel.getAttribute('data-dashboard-panel') === visao;
                panel.classList.toggle('is-active', ativo);
            });

            window.requestAnimationFrame(function () {
                renderPanelCharts(visao);
            });
        }

        select.addEventListener('change', aplicar);
        aplicar();
    }

    function initDashboardCharts() {
        if (typeof Chart === 'undefined') {
            console.error('Chart.js não carregado.');
            document.querySelectorAll('[data-chart-wrapper]').forEach(function (wrapper) {
                setChartState(wrapper.getAttribute('data-chart-wrapper'), 'error', 'Erro ao carregar gráfico.');
            });
            return;
        }

        registrarDataLabels();

        try {
            payloadCache = parsePayload();
        } catch (err) {
            console.error(err);
            document.querySelectorAll('[data-chart-wrapper]').forEach(function (wrapper) {
                setChartState(wrapper.getAttribute('data-chart-wrapper'), 'error', 'Erro ao carregar dados dos gráficos.');
            });
            return;
        }

        initVisaoGraficos();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initDashboardCharts);
    } else {
        initDashboardCharts();
    }
})();
