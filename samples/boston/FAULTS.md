# Seeded faults

Applied after subway filtering with seed `20261122`; nothing else is altered.

| kind | run_id | capture_id | detail |
| --- | --- | --- | --- |
| duplicate_capture | 01M81V9WP5XKJ8S30877Z4ZRSD | trip_updates-20261122T144200Z-bf11ca87 | bytes of run 01M81V8235MT7XV1E67ESNSPFN |
| duplicate_capture | 01M81WGB559BCRFT74TBM3QMZF | trip_updates-20261122T150300Z-f5035af5 | bytes of run 01M81WEGJ320FC51H4WGG5572N |
| corrupt_trip_updates | 01M81X4FP5QQE2D4SQZNVSKN5B | trip_updates-20261122T151400Z-8f71e6b9 | truncated 53677 -> 38323 bytes |
| vehicle_out_of_bbox | 01M81XK4E5WHRA48XQMJRJCJYA | vehicle_positions-20261122T152200Z-53d5ebb0 | vehicle G-10098 -> (51.5074, -0.1278) |
| vehicle_out_of_bbox | 01M81XW9D5GDA7S4BJXP6C5157 | vehicle_positions-20261122T152700Z-12b0b1d6 | vehicle G-10019 -> (48.8566, 2.3522) |
| vehicle_out_of_bbox | 01M81XBT254AYNBYP9A3PMN0Q0 | vehicle_positions-20261122T151800Z-123b065f | vehicle O-548CCCCD -> (0.0, 0.0) |
| trip_id_cleared | 01M81V0QQ5W6MYGM6SZXJ94Q54 | trip_updates-20261122T143700Z-9ef86a89 | entity 77658381 (was trip 77658381), 24 stop updates |
| trip_id_cleared | 01M81WYZX55ERC447DVT74ACZ0 | trip_updates-20261122T151100Z-6185bb82 | entity 77658378 (was trip 77658378), 17 stop updates |
| missing_minute | 01M81Y78ZARQNZKA3Q18STB360 | - | scheduled 2026-11-22T15:33:00Z |

## Counts

- corrupt_trip_updates: 1
- duplicate_capture: 2
- missing_minute: 1
- trip_id_cleared: 2
- vehicle_out_of_bbox: 3
