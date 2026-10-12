# Friss páros modellmérés – v0.18.0

A jelenlegi modell és koordinátor két parkolókiosztását 9 s, illetve 3 s felkínálási közzel hasonlítottuk össze. Konfigurációnként 1 független futáspár (seed 1001–1001), páronként ugyanaz a hat autó, 16 parkoló és két zebra szerepel. A megfigyelési felső határ 600 s; mind a hat autó beparkolása után a számolás lezárul, mert a célmutatók ekkor már változatlanok. Ez csak futási időt becslő pilot, a végleges kampánytól külön kezeljük.

| Forgatókönyv | Mutató | nearest − fixed | Páros bootstrap 95%-os intervallum |
|---|---|---:|---:|
| Referencia (9 s) | Parkolási idő (s/autó) | -5.558 | nincs |
| Referencia (9 s) | Úthossz (m/autó) | -16.667 | nincs |
| Nagyobb terhelés (3 s) | Parkolási idő (s/autó) | -5.558 | nincs |
| Nagyobb terhelés (3 s) | Úthossz (m/autó) | -16.667 | nincs |

A negatív érték a `nearest` szabály javára mutat. A parkolási idő a termelési felkínálástól indul, és az udvaron kívüli pufferben töltött időt is tartalmazza. Az újramintázási egység egy seedhez tartozó futáspár hat páros autókülönbségének átlaga; 10000 bootstrap-újramintázás készült, a telemetriapontok nem független minták.

Összesen 12 párosan befejezett autó, 0 be nem fejezett autófutás és 0 referenciaellenőrzéssel észlelt ütközés szerepel. Minden felkínálási idő egyezett, minden megfigyelési ablakba eső ütemezett gyalogos ténylegesen létrejött, a közös ablakban az érkezések egyeztek, és a foglalási invariánsok minden végrehajtott időlépésnél teljesültek. A későbbi, már befejezett kohorsz utáni gyalogosesemények külön „nem megfigyelt” jelölést kaptak. A forrás- és konfigurációhash-ek a kampány előtt és után egyeztek.

Ez determinisztikus, memórián belüli protokollkézbesítéssel végzett modellkísérlet. Az éles MQTT-futtató nem változott; hálózati késleltetést, veszteséget és valódi gyári teljesítményt ez a mérés nem igazol. A közös exogén gyalogosérkezések mellett a kooperatív átkelés kezdete és a járművek tényleges látótávolsági helyzete endogén. A runner azonos kulcsú virtuális mérési zajt alkalmaz ugyanazzal a ±0,1 m korláttal. A korábbi −7,992 s eredmény más kampányból származik, itt nem használjuk új mérésként.

Proveniencia és részletes összesítés: `summary.json`; nyers futások: `runs.jsonl` és `runs.csv`; párok: `pairs.json` és `pairs.csv`; autók: `vehicle_results.csv`; közös külső események: `exogenous_schedules.json`; részletes esemény- és döntési naplók: `raw/`. A pontos mérési határt a `docs/VEGLEGES_MERESI_TERV.md` rögzíti.
