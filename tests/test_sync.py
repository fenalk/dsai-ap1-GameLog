from datetime import date
from types import SimpleNamespace

import pytest

from app import sync
from app.fontes import FONTES
from app.models import Game
from app.sync import FonteIndisponivel, sincronizar


def item(i="1", **kw):
    base = dict(
        external_id=i,
        title=f"Jogo Sinc {i}",
        synopsis="Sinopse original.",
        release_date=date(2021, 5, 4),
        developer="Estúdio Sync",
        genres=["Ação", "RPG"],
        platforms=["Windows"],
        cover_url=f"https://img/{i}.jpg",
    )
    return {**base, **kw}


def fonte(*itens, erro=None):
    def listar(limite, termo=None):
        if erro:
            raise OSError(erro)
        return list(itens)[:limite]

    return SimpleNamespace(listar=listar)


@pytest.fixture
def registrar_fonte():
    criadas = []

    def registrar(nome, *itens, erro=None):
        FONTES[nome] = fonte(*itens, erro=erro)
        criadas.append(nome)
        return nome

    yield registrar
    for nome in criadas:
        FONTES.pop(nome, None)


def test_sinc01_cria_jogo_novo_e_segunda_execucao_nao_duplica(app):
    with app.app_context():
        assert sincronizar("falsa", modulo=fonte(item("1"))) == (1, 0, 0, 0)
        g = Game.query.one()
        assert (g.source, g.external_id, g.title) == ("falsa", "1", "Jogo Sinc 1")
        assert g.slug and g.developer.name == "Estúdio Sync"

        assert sincronizar("falsa", modulo=fonte(item("1"))) == (0, 0, 1, 0)
        assert Game.query.count() == 1


def test_sinc02_atualiza_metadados_sem_trocar_slug_nem_duplicar(app):
    with app.app_context():
        assert sincronizar("falsa", modulo=fonte(item("1"))) == (1, 0, 0, 0)
        slug = Game.query.one().slug

        alterado = item(
            "1",
            title="Jogo Sinc 1 Renomeado",
            synopsis="Sinopse nova.",
            release_date=date(2022, 1, 2),
            developer="Outro Estúdio",
            genres=["Aventura"],
            platforms=["Linux"],
            cover_url="https://img/novo.jpg",
        )
        assert sincronizar("falsa", modulo=fonte(alterado)) == (0, 1, 0, 0)

        g = Game.query.one()
        assert g.slug == slug
        assert g.title == "Jogo Sinc 1 Renomeado" and g.synopsis == "Sinopse nova."
        assert g.release_date == date(2022, 1, 2)
        assert g.developer.name == "Outro Estúdio"
        assert [x.name for x in g.genres] == ["Aventura"]
        assert [x.name for x in g.platforms] == ["Linux"]
        assert g.cover_url == "https://img/novo.jpg"


def test_sinc03_item_invalido_item_com_erro_e_item_valido_na_mesma_execucao(app):
    with app.app_context():
        resultado = sincronizar(
            "falsa",
            modulo=fonte(
                {"erro": "falhou"},
                item("1", title=""),
                item("2", release_date=None),
                item("3", release_date="data inválida"),
                item("4"),
            ),
        )
        assert resultado == (1, 0, 3, 1)
        g = Game.query.one()
        assert g.external_id == "4"


def test_sinc04_fonte_fora_do_ar_exit_code_1(app, registrar_fonte):
    nome = registrar_fonte("fora-do-ar", erro="sem rede")
    r = app.test_cli_runner().invoke(args=["sync-games", "--fonte", nome])
    assert r.exit_code == 1
    assert r.output == "erro=fonte indisponivel: sem rede\n"


def test_sinc05_saida_exata_do_comando(app, registrar_fonte):
    nome = registrar_fonte("falsa-cli", item("1"), item("2", title=""))
    r = app.test_cli_runner().invoke(args=["sync-games", "--fonte", nome, "--limite", "2"])
    assert r.exit_code == 0
    assert r.output == "novos=1 atualizados=0 ignorados=1 falhas=0\n"


def test_sinc06_fonte_desconhecida_e_erro_de_uso(app):
    r = app.test_cli_runner().invoke(args=["sync-games", "--fonte", "nao-existe"])
    assert r.exit_code != 0
    assert "fonte desconhecida" in r.output


def test_sinc07_mesmo_external_id_em_outra_fonte_e_outro_jogo(app):
    with app.app_context():
        assert sincronizar("fonte-a", modulo=fonte(item("7"))) == (1, 0, 0, 0)
        assert sincronizar("fonte-b", modulo=fonte(item("7"))) == (1, 0, 0, 0)
        assert Game.query.count() == 2
        assert {g.source for g in Game.query.all()} == {"fonte-a", "fonte-b"}


def test_sinc08_falha_de_banco_em_um_item_faz_rollback_e_continua(app, monkeypatch):
    original = sync._criar
    chamadas = {"n": 0}

    def intermitente(*args, **kwargs):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise RuntimeError("falha de banco")
        return original(*args, **kwargs)

    monkeypatch.setattr(sync, "_criar", intermitente)
    with app.app_context():
        assert sincronizar("falsa", modulo=fonte(item("1"), item("2"))) == (1, 0, 0, 1)
        assert [g.external_id for g in Game.query.all()] == ["2"]


def test_sinc09_nao_apaga_jogo_removido_nem_muda_removido_at(app):
    from datetime import datetime

    from app.models import db

    with app.app_context():
        assert sincronizar("falsa", modulo=fonte(item("1"))) == (1, 0, 0, 0)
        g = Game.query.one()
        g.removed_at = datetime(2024, 1, 1, 12, 0, 0)
        g.slug = "slug-antigo"
        db.session.commit()

        assert sincronizar("falsa", modulo=fonte(item("1", title="Outro título"))) == (0, 1, 0, 0)
        g = Game.query.one()
        assert g.slug == "slug-antigo" and g.removed_at == datetime(2024, 1, 1, 12, 0, 0)
        assert g.title == "Outro título"


def test_sinc10_termo_e_limite_sao_repassados_para_a_fonte(app):
    recebido = {}

    def listar(limite, termo=None):
        recebido["limite"], recebido["termo"] = limite, termo
        return [item("1")]

    with app.app_context():
        assert sincronizar("falsa", limite=3, termo="zelda", modulo=SimpleNamespace(listar=listar)) == (1, 0, 0, 0)
        assert recebido == {"limite": 3, "termo": "zelda"}


def test_sinc11_fonte_indisponivel_levanta_excecao_tipificada():
    with pytest.raises(FonteIndisponivel):
        sincronizar("falsa", modulo=fonte(erro="caiu"))
