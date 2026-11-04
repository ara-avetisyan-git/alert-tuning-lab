# Data dictionary

All data is **synthetic**. Names are random combinations of common given names and surnames; any resemblance to a real person
or company is coincidental. Amounts are in Armenian dram (AMD). Money values are integers.

## customers.csv
| Column | Meaning |
|---|---|
| customer_id | `C00001` ... |
| name, name_hy | Name in Latin and Armenian script (individuals and fictional companies) |
| customer_type | `individual` or `legal_entity` |
| segment | `pensioner`, `salaried`, `migrant_family`, `small_business`, `student` |
| region, home_branch_id | Region (marz) and the branch where the customer was onboarded |
| onboarding_date | Date the relationship started |
| expected_monthly_amd | Expected monthly turnover declared at onboarding (the customer profile) |
| risk_rating | `low` / `medium` from the segment |

## branches.csv
`branch_id`, `region`, `branch_type` (`urban` / `rural`).

## transactions.csv
One row per customer **side** of a transaction. A transfer between two customers appears twice (one `out`, one `in`) with the same `transfer_id`.

| Column | Meaning |
|---|---|
| txn_id, timestamp | Unique id; local time `YYYY-MM-DD HH:MM:SS` |
| customer_id, branch_id, channel | Customer, branch where the operation took place, `counter` or `mobile` |
| product | `pension_payout`, `utility_payment`, `utility_refund`, `cash_deposit`, `cash_withdrawal`, `domestic_transfer`, `international_transfer` |
| direction | `in` (money to the customer) or `out` |
| amount_amd, currency | Amount in AMD; `currency` is the original currency label for international transfers |
| counterparty_id, counterparty_country | Other side: a customer id, an external id (`EXT...`, `EXTS...`, `EXTR...`) or an institution (`PENSION-FUND`, `UTILITY-CO`) |
| transfer_id | Links the two legs of a transfer between customers |
| is_suspicious | **Ground truth.** 1 if the row belongs to a planted suspicious scenario |
| typology | Typology of the planted scenario (empty when `is_suspicious` = 0) |
| lookalike | Typology a benign look-alike resembles (empty otherwise) |
| scenario_id | `S....` planted suspicious scenario, `L....` benign look-alike, empty for normal activity |

## scenarios.csv
`scenario_id`, `kind` (`suspicious` / `lookalike`), `typology`, `customer_ids` (`;` separated, every participant), `start`, `end`,
`txn_count`, `total_amount_amd`, `description`.

## Typologies
`structuring`, `fan_in_mule`, `fan_out`, `pass_through`, `dormant_wakeup`, `branch_hopping` (postal: the same pension paid at several regions' counters within hours),
`circular`, `overpayment_refund` (postal: large utility payment then refund), `profile_mismatch`.

## Notes
- The reporting threshold (`threshold_amd`) is **illustrative** and configurable; it is not a statement of any legal limit.
- Labels describe planted scenarios, not legal conclusions. Normal activity is `is_suspicious = 0` even if a rule would alert on it.
