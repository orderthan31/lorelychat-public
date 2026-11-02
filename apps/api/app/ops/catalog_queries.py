"""DB-side catalog pages, preserving Unicode substring search semantics."""
from sqlalchemy import func, literal
from sqlmodel import select


def catalog_page(session, model, *, fields, order, page, page_size, q=None, predicates=()):
    # SQLite's built-in lower() is ASCII-only. Use the old Python lower()
    # semantics in SQLite without fetching authored rows into Python.
    connection=session.connection().connection.driver_connection
    connection.create_function('lorechat_lower',1,lambda value: str(value or '').lower(),deterministic=True)
    predicates=list(predicates)
    search=(q or '').strip().lower()
    if search:
        joined=func.coalesce(fields[0],'')
        for column in fields[1:]:joined=joined+literal(' ')+func.coalesce(column,'')
        # instr() is literal substring matching: '%'/'_' are not wildcard input.
        predicates.append(func.instr(func.lorechat_lower(joined),search)>0)
    total=session.exec(select(func.count()).select_from(model).where(*predicates)).one()
    pages=max(1,(total+page_size-1)//page_size);safe_page=min(page,pages)
    rows=session.exec(select(model).where(*predicates).order_by(*order).offset((safe_page-1)*page_size).limit(page_size)).all()
    return {'items':list(rows),'total':total,'page':safe_page,'page_size':page_size,'pages':pages}
