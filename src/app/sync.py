"""Sincronização idempotente do catálogo com fontes externas (executada por cron, fora do processo web)."""
from datetime import date, datetime

from .fontes import FONTES
from .models import Developer, Game, Genre, Platform, db
from .util import slugify

SEM_SINOPSE = "Sem descrição."
SEM_DESENVOLVEDORA = "Desenvolvedora desconhecida"


class FonteIndisponivel(RuntimeError):
    """A fonte falhou ao entregar os itens; não há como sincronizar."""


def _data(valor):
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str):
        try:
            return date.fromisoformat(valor.strip())
        except ValueError:
            return None
    return None


def _nomes(valores):
    vistos, nomes = set(), []
    for valor in valores or []:
        nome = str(valor).strip()[:80]
        if nome and slugify(nome) and slugify(nome) not in vistos:
            vistos.add(slugify(nome))
            nomes.append(nome)
    return nomes


def _obter(model, nome):
    obj = model.query.filter_by(slug=slugify(nome)).first()
    if not obj:
        obj = model(name=nome[:80], slug=slugify(nome))
        db.session.add(obj)
        db.session.flush()
    return obj


def _slug_livre(titulo, fonte, external_id):
    """Slug só é escolhido na criação; jogos existentes mantêm a URL (nunca muda)."""
    base = (slugify(titulo) or slugify(external_id) or "jogo")[:150]
    slug = base
    if Game.query.filter_by(slug=slug).first():
        sufixo = f"{slugify(fonte) or 'fonte'}-{slugify(external_id) or 'id'}"
        slug, n = f"{base}-{sufixo}"[:170], 1
        while Game.query.filter_by(slug=slug).first():
            n += 1
            slug = f"{base}-{sufixo}-{n}"[:170]
    return slug


def _criar(fonte, j, titulo, lancamento, external_id):
    game = Game(
        title=titulo[:150],
        slug=_slug_livre(titulo, fonte, external_id),
        synopsis=(j.get("synopsis") or "").strip() or SEM_SINOPSE,
        release_date=lancamento,
        developer=_obter(Developer, (j.get("developer") or "").strip()[:80] or SEM_DESENVOLVEDORA),
        genres=[_obter(Genre, nome) for nome in _nomes(j.get("genres"))],
        platforms=[_obter(Platform, nome) for nome in _nomes(j.get("platforms"))],
        source=fonte,
        external_id=external_id,
        cover_url=(j.get("cover_url") or "").strip() or None,
    )
    db.session.add(game)
    db.session.flush()
    return game


def _atualizar(game, j, titulo, lancamento):
    """Atualiza apenas os campos mutáveis; devolve True se algum valor mudou."""
    mudou = False

    if game.title != titulo[:150]:
        game.title = titulo[:150]
        mudou = True
    if game.release_date != lancamento:
        game.release_date = lancamento
        mudou = True

    if "synopsis" in j:
        sinopse = (j.get("synopsis") or "").strip() or SEM_SINOPSE
        if game.synopsis != sinopse:
            game.synopsis = sinopse
            mudou = True

    if "developer" in j:
        nome = (j.get("developer") or "").strip()[:80] or SEM_DESENVOLVEDORA
        if not game.developer or slugify(game.developer.name) != slugify(nome):
            game.developer = _obter(Developer, nome)
            mudou = True

    if "genres" in j:
        nomes = _nomes(j.get("genres"))
        if {slugify(g.name) for g in game.genres} != {slugify(n) for n in nomes}:
            game.genres = [_obter(Genre, n) for n in nomes]
            mudou = True

    if "platforms" in j:
        nomes = _nomes(j.get("platforms"))
        if {slugify(p.name) for p in game.platforms} != {slugify(n) for n in nomes}:
            game.platforms = [_obter(Platform, n) for n in nomes]
            mudou = True

    if "cover_url" in j:
        capa = (j.get("cover_url") or "").strip() or None
        if game.cover_url != capa:
            game.cover_url = capa
            mudou = True

    return mudou


def _sincronizar_item(fonte, j):
    if "erro" in j:
        return "falha"

    external_id = str(j.get("external_id") or "").strip()
    titulo = str(j.get("title") or "").strip()
    lancamento = _data(j.get("release_date"))
    if not external_id or not titulo or lancamento is None:
        return "ignorado"

    game = Game.query.filter_by(source=fonte, external_id=external_id).first()
    if game is None:
        _criar(fonte, j, titulo, lancamento, external_id)
        db.session.commit()
        return "novo"

    if _atualizar(game, j, titulo, lancamento):
        db.session.commit()
        return "atualizado"
    return "ignorado"


def _motivo(exc):
    return str(exc).strip() or exc.__class__.__name__


def sincronizar(fonte, limite=40, termo=None, modulo=None):
    """Devolve (novos, atualizados, ignorados, falhas); `modulo` injeta a fonte falsa nos testes.

    Falha da fonte (listar/iteração) levanta FonteIndisponivel; falha de um item é isolada com
    rollback, conta como falha e a sincronização continua.
    """
    modulo = modulo or FONTES[fonte]

    try:
        itens = iter(modulo.listar(limite, termo))
    except Exception as exc:
        raise FonteIndisponivel(_motivo(exc)) from exc

    novos = atualizados = ignorados = falhas = 0
    while True:
        try:
            j = next(itens)
        except StopIteration:
            break
        except Exception as exc:
            raise FonteIndisponivel(_motivo(exc)) from exc

        try:
            status = _sincronizar_item(fonte, j)
        except Exception:
            db.session.rollback()
            falhas += 1
            continue

        if status == "novo":
            novos += 1
        elif status == "atualizado":
            atualizados += 1
        elif status == "ignorado":
            ignorados += 1
        else:
            falhas += 1

    return novos, atualizados, ignorados, falhas
