from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from core.choices import StatusHomologacao
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from inventario.models import (
    CicloInventario,
    CicloInventarioItem,
    CicloInventarioSku,
    InventarioItem,
)
from posicoes.models import Posicao
from produtos.models import Produto


class Command(BaseCommand):
    help = 'Audita integridade estrutural do banco de dados do inventário.'

    def handle(self, *args, **options):
        achados = []
        achados.extend(self._auditar_duplicatas())
        achados.extend(self._auditar_orfaos())
        achados.extend(self._auditar_estoque())
        achados.extend(self._auditar_ciclos())
        achados.extend(self._auditar_cadastros())

        if not achados:
            self.stdout.write(self.style.SUCCESS('Nenhuma inconsistência estrutural encontrada.'))
            return

        for severidade, mensagem in achados:
            estilo = {
                'CRITICO': self.style.ERROR,
                'ALTO': self.style.WARNING,
                'MEDIO': self.style.NOTICE,
            }.get(severidade, self.style.NOTICE)
            self.stdout.write(estilo(f'[{severidade}] {mensagem}'))

        criticos = sum(1 for s, _ in achados if s == 'CRITICO')
        self.stdout.write('')
        self.stdout.write(f'Total: {len(achados)} achado(s), {criticos} crítico(s).')

    def _auditar_duplicatas(self) -> list[tuple[str, str]]:
        achados = []

        dup_sap = (
            EstoqueSAP.objects.values('produto_id')
            .annotate(total=Count('id'))
            .filter(total__gt=1)
        )
        for item in dup_sap:
            achados.append((
                'ALTO',
                f'EstoqueSAP duplicado para produto_id={item["produto_id"]} '
                f'({item["total"]} registros).',
            ))

        dup_ef = (
            EstoqueFisico.objects.values('produto_id', 'posicao_id')
            .annotate(total=Count('id'))
            .filter(total__gt=1)
        )
        for item in dup_ef:
            achados.append((
                'CRITICO',
                f'EstoqueFisico duplicado produto_id={item["produto_id"]} '
                f'posicao_id={item["posicao_id"]} ({item["total"]} registros).',
            ))

        dup_ciclo_sku = (
            CicloInventarioSku.objects.values('ciclo_id', 'produto_id')
            .annotate(total=Count('id'))
            .filter(total__gt=1)
        )
        for item in dup_ciclo_sku:
            achados.append((
                'CRITICO',
                f'CicloInventarioSku duplicado ciclo_id={item["ciclo_id"]} '
                f'produto_id={item["produto_id"]}.',
            ))

        return achados

    def _auditar_orfaos(self) -> list[tuple[str, str]]:
        achados = []

        itens_sem_sku = CicloInventarioItem.objects.filter(ciclo_sku__isnull=True).count()
        if itens_sem_sku:
            achados.append((
                'MEDIO',
                f'{itens_sem_sku} CicloInventarioItem(s) sem ciclo_sku vinculado.',
            ))

        itens_inativos = InventarioItem.objects.filter(
            Q(produto__ativo=False) | Q(posicao__ativo=False),
        ).count()
        if itens_inativos:
            achados.append((
                'MEDIO',
                f'{itens_inativos} InventarioItem(s) referenciam produto/posição inativo(s).',
            ))

        return achados

    def _auditar_estoque(self) -> list[tuple[str, str]]:
        achados = []

        negativos_fisico = EstoqueFisico.objects.filter(quantidade__lt=Decimal('0')).count()
        if negativos_fisico:
            achados.append((
                'CRITICO',
                f'{negativos_fisico} registro(s) de EstoqueFisico com quantidade negativa.',
            ))

        negativos_sap = EstoqueSAP.objects.filter(total__lt=Decimal('0')).count()
        if negativos_sap:
            achados.append((
                'ALTO',
                f'{negativos_sap} registro(s) de EstoqueSAP com total negativo.',
            ))

        negativos_contagem = InventarioItem.objects.filter(
            quantidade_fisica__lt=Decimal('0'),
        ).count()
        if negativos_contagem:
            achados.append((
                'ALTO',
                f'{negativos_contagem} InventarioItem(s) com quantidade_fisica negativa.',
            ))

        produtos_sem_embalagem = Produto.objects.filter(
            ativo=True,
            embalagem='',
        ).count()
        if produtos_sem_embalagem:
            achados.append((
                'BAIXO',
                f'{produtos_sem_embalagem} produto(s) ativo(s) sem embalagem cadastrada.',
            ))

        return achados

    def _auditar_ciclos(self) -> list[tuple[str, str]]:
        achados = []

        ciclos_ativos = CicloInventario.objects.filter(
            ativo=True,
            status_ciclo=CicloInventario.StatusCiclo.ATIVO,
        ).count()
        if ciclos_ativos > 1:
            achados.append((
                'ALTO',
                f'{ciclos_ativos} ciclos cíclicos ATIVOS simultaneamente (esperado: 0 ou 1).',
            ))

        skus_sem_posicao = CicloInventarioSku.objects.annotate(
            total_pos=Count('posicoes'),
        ).filter(total_pos=0).count()
        if skus_sem_posicao:
            achados.append((
                'MEDIO',
                f'{skus_sem_posicao} SKU(s) cíclico(s) sem posições vinculadas.',
            ))

        return achados

    def _auditar_cadastros(self) -> list[tuple[str, str]]:
        achados = []

        produtos_precadastro = Produto.objects.filter(
            status_homologacao=StatusHomologacao.PENDENTE,
        ).count()
        posicoes_precadastro = Posicao.objects.filter(
            status_homologacao=StatusHomologacao.PENDENTE,
        ).count()
        if produtos_precadastro or posicoes_precadastro:
            achados.append((
                'BAIXO',
                f'Pré-cadastros pendentes: {produtos_precadastro} produto(s), '
                f'{posicoes_precadastro} posição(ões).',
            ))

        return achados
