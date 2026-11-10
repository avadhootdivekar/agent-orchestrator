# Portfolio report

Subjects: claude-haiku, claude-sonnet, claude-opus, ao-overseer-sonnet. Tasks pooled over repeats; infra errors, budget skips and audit-flagged tasks are not attempts.

## Overall

Cell: mean score · solved/attempted · cost · $/solved.

| subject | claude-haiku | claude-sonnet | claude-opus | ao-overseer-sonnet |
|---|---|---|---|---|
| all | 0.885 · 110/137 · $3.13 · $0.03/solved | 0.937 · 110/125 · $10.36 · $0.09/solved | 0.992 · 135/137 · $31.96 · $0.24/solved | 0.980 · 73/77 · $102.13 · $1.40/solved |

## Per suite

Cell: mean score · solved/attempted · cost · $/solved.

| suite | claude-haiku | claude-sonnet | claude-opus | ao-overseer-sonnet |
|---|---|---|---|---|
| gen-calib-ah | 1.000 · 2/2 · $0.07 · $0.03/solved | — | 1.000 · 2/2 · $0.94 · $0.47/solved | — |
| gen-calib-ap | 1.000 · 2/2 · $0.05 · $0.03/solved | — | 1.000 · 2/2 · $0.87 · $0.44/solved | — |
| gen-calib-bh | 1.000 · 2/2 · $0.28 · $0.14/solved | — | 1.000 · 2/2 · $1.24 · $0.62/solved | — |
| gen-calib-bh2 | 1.000 · 2/2 · $0.66 · $0.33/solved | — | 1.000 · 2/2 · $1.60 · $0.80/solved | — |
| gen-calib-ob | 1.000 · 2/2 · $0.36 · $0.18/solved | — | 1.000 · 2/2 · $1.56 · $0.78/solved | — |
| gen-calib-re | 1.000 · 2/2 · $0.10 · $0.05/solved | — | 1.000 · 2/2 · $0.94 · $0.47/solved | — |
| polyglot-b | 0.776 · 28/48 · $0.76 · $0.03/solved | 0.881 · 37/48 · $4.14 · $0.11/solved | 0.977 · 46/48 · $10.77 · $0.23/solved | — |
| polyglot-bv | 1.000 · 48/48 · $0.45 · $0.01/solved | 1.000 · 48/48 · $3.81 · $0.08/solved | 1.000 · 48/48 · $8.13 · $0.17/solved | 1.000 · 48/48 · $61.58 · $1.28/solved |
| polyglot-mini | 0.827 · 22/29 · $0.40 · $0.02/solved | 0.926 · 25/29 · $2.40 · $0.10/solved | 1.000 · 29/29 · $5.90 · $0.20/solved | 0.947 · 25/29 · $40.55 · $1.62/solved |

## Per area (family)

Cell: mean score · solved/attempted · cost · $/solved.

| area | claude-haiku | claude-sonnet | claude-opus | ao-overseer-sonnet |
|---|---|---|---|---|
| algo-hard | 1.000 · 2/2 · $0.07 · $0.03/solved | — | 1.000 · 2/2 · $0.94 · $0.47/solved | — |
| algo-puzzle | 1.000 · 2/2 · $0.05 · $0.03/solved | — | 1.000 · 2/2 · $0.87 · $0.44/solved | — |
| bug-hunt | 1.000 · 4/4 · $0.93 · $0.23/solved | — | 1.000 · 4/4 · $2.84 · $0.71/solved | — |
| exercism | 0.874 · 98/125 · $1.62 · $0.02/solved | 0.937 · 110/125 · $10.36 · $0.09/solved | 0.991 · 123/125 · $24.81 · $0.20/solved | 0.980 · 73/77 · $102.13 · $1.40/solved |
| order-book | 1.000 · 2/2 · $0.36 · $0.18/solved | — | 1.000 · 2/2 · $1.56 · $0.78/solved | — |
| rules-engine | 1.000 · 2/2 · $0.10 · $0.05/solved | — | 1.000 · 2/2 · $0.94 · $0.47/solved | — |

## Per language

Cell: mean score · solved/attempted · cost · $/solved.

| language | claude-haiku | claude-sonnet | claude-opus | ao-overseer-sonnet |
|---|---|---|---|---|
| cpp | 0.731 · 19/26 · $0.37 · $0.02/solved | 0.885 · 23/26 · $2.40 · $0.10/solved | 1.000 · 26/26 · $6.24 · $0.24/solved | 1.000 · 16/16 · $21.93 · $1.37/solved |
| go | 0.836 · 15/22 · $0.26 · $0.02/solved | 0.900 · 18/22 · $1.93 · $0.11/solved | 0.955 · 21/22 · $4.48 · $0.21/solved | 0.929 · 13/14 · $19.36 · $1.49/solved |
| javascript | 0.856 · 19/25 · $0.34 · $0.02/solved | 0.942 · 21/25 · $1.88 · $0.09/solved | 0.995 · 24/25 · $4.28 · $0.18/solved | 1.000 · 15/15 · $18.99 · $1.27/solved |
| python | 0.968 · 35/38 · $1.75 · $0.05/solved | 0.960 · 24/26 · $1.90 · $0.08/solved | 1.000 · 38/38 · $11.32 · $0.30/solved | 0.969 · 14/16 · $20.38 · $1.46/solved |
| rust | 0.986 · 22/26 · $0.41 · $0.02/solved | 0.995 · 24/26 · $2.24 · $0.09/solved | 1.000 · 26/26 · $5.65 · $0.22/solved | 0.996 · 15/16 · $21.46 · $1.43/solved |

## Item difficulty (tasks every subject attempted)

Band k = solved by k of the 4 subjects. Mass in the middle bands = discrimination; all in band 4 = saturation; all in band 0 = floor.

| band (solved by) | tasks | share | per suite |
|---|---|---|---|
| 4/4 | 68 | 88% | polyglot-bv: 48, polyglot-mini: 20 |
| 3/4 | 5 | 6% | polyglot-mini: 5 |
| 2/4 | 2 | 3% | polyglot-mini: 2 |
| 1/4 | 2 | 3% | polyglot-mini: 2 |
| 0/4 | 0 | 0% | — |

### Tasks that separate subjects (bands 1..N-1)

- `polyglot-mini/ex-cpp-diamond` — solved by claude-sonnet, claude-opus, ao-overseer-sonnet
- `polyglot-mini/ex-cpp-sublist` — solved by claude-sonnet, claude-opus, ao-overseer-sonnet
- `polyglot-mini/ex-go-connect` — solved by claude-sonnet, claude-opus, ao-overseer-sonnet
- `polyglot-mini/ex-python-dominoes` — solved by claude-haiku, claude-sonnet, claude-opus
- `polyglot-mini/ex-python-grade-school` — solved by claude-sonnet, claude-opus, ao-overseer-sonnet
- `polyglot-mini/ex-cpp-phone-number` — solved by claude-opus, ao-overseer-sonnet
- `polyglot-mini/ex-python-pov` — solved by claude-haiku, claude-opus
- `polyglot-mini/ex-go-trinary` — solved by claude-opus
- `polyglot-mini/ex-rust-scale-generator` — solved by claude-opus
