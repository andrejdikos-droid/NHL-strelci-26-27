# NHL Strelci 2026/27

Verejný dashboard pre 11-členný NHL goal draft.

## Funkcie
- regular-season góly všetkých 88 draftovaných hráčov z NHL Stats API,
- denné automatické prepočítanie tímov,
- hrubý zisk, strata a NET bilancia,
- zoznam strelcov od posledného update,
- história NET bilancie,
- dátový model pripravený na 4 trejdy na manažéra.

## Finančný model
Pri 11 manažéroch a 1 € za gól:
- hrubý zisk = vlastné góly × 10 €
- strata = góly všetkých súperov × 1 €
- NET = 11 × vlastné góly − všetky góly v lige

Súčet NET bilancií všetkých manažérov je vždy 0 €.

## Automatická aktualizácia
`.github/workflows/update-stats.yml` beží denne o 08:15 UTC. Dá sa spustiť aj ručne cez **Actions → Update NHL stats → Run workflow**.

## GitHub Pages
V repozitári nastav:
**Settings → Pages → Build and deployment → Source: Deploy from a branch → Branch: main / (root) → Save**

Výsledná adresa bude:
`https://andrejdikos-droid.github.io/NHL-strelci-26-27/`

## Trejdy
Každý slot v `data/league.json` obsahuje:
- `player` – aktuálny hráč,
- `bankedGoals` – už získané góly predchádzajúcich hráčov v slote,
- `goalsAtAcquisition` – sezónne góly nového hráča v momente trejdu.

Vďaka tomu staré góly zostanú manažérovi a nový hráč sa ráta až od momentu výmeny.
