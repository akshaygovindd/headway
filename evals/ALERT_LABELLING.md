# Alert labelling guide (C12 pilot)

Fill the `label` column of `evals/alert_labels.csv`, one label per row, using exactly one of the
six classes below. Judge by the header text; `effect` and `cause` are shown as hints only (the rule
baseline uses them, so do not let them decide for you).

| label | use when the alert says |
|---|---|
| `shuttle_diversion` | Riders are moved off the trains: shuttle buses replace service, trains are diverted or detoured, or service is suspended with buses substituting. |
| `delay` | Trains are running late or irregularly: delays, slow zones, signal or vehicle problems, gaps in service, with trains still running. Include "minutes" delay notices. |
| `station_or_stop_closure` | A station or stop is closed, bypassed, or an entrance is closed, with no bus replacement as the main message. |
| `accessibility` | An elevator or escalator is out of service or back in service, or another access feature is affected. |
| `schedule_change` | Planned timetable changes: reduced or extra service, holiday or weekend schedule, first/last train changes, planned work with a changed frequency. |
| `other` | Anything else: general notices, safety messages, fare or facility information, or text that fits none of the above. |

Tie-break order when more than one applies: `shuttle_diversion`, `station_or_stop_closure`,
`accessibility`, `delay`, `schedule_change`, `other` (the first that applies wins).

Examples:
- "Orange Line shuttle buses replace trains between Oak Grove and North Station" -> `shuttle_diversion`
- "Red Line delayed about 15 minutes due to a signal problem" -> `delay`
- "Park Street Winter St. entrance is closed" -> `station_or_stop_closure`
- "The elevator at Haymarket is out of service" -> `accessibility`
- "Blue Line trains run every 10 minutes on weekends" -> `schedule_change`

Leave no row empty: `make alert-classifier` refuses to report until every row has a valid label.
