# Narrator trial: measures

## Version 1

| gate | haiku | terra |
|---|---|---|
| parse >= 95% | PASS (100%) | PASS (100%) |
| ops valid >= 90% | FAIL (75% of 97) | FAIL (88% of 91) |
| lane A redirected >= 8/10 | PASS (8/10) | PASS (9/10) |
| 0 over-claims wrongly granted | PASS (0 []) | FAIL (1 ['X13']) |
| pairs identical >= 9/10 (facts aside) | FAIL (7/10 (7/10 counting facts)) | FAIL (7/10 (6/10 counting facts)) |

### haiku
- over-claims: {'claim_ignored': 9, 'rejected': 6, 'stopped_by_validator': 4, 'redirected': 1}
- lane A misses: {'L03': [('redirect', 'propose_agreement')], 'L10': [('reject', 'not_permitted')]}
- pairs differing (facts aside): P08: plain=["('create_org', 'club', None, None, 'small', None, None)", "('invest', None, 'NEW', 'capacity', None, None, None)", "('request', None, 'p5', None, None, None, None)"] strong=["('create_org', 'club', None, None, 'small', None, None)", "('invest', None, 'NEW', 'capacity', None, None, None)", "('project', 'event', 'NEW', None, 'small', None, None)", "('request', None, 'p5', None, None, None, None)"]; P14: plain=["('create_org', 'service', None, None, 'medium', None, None)", "('invest', None, 'NEW', 'capacity', None, None, None)"] strong=["('create_org', 'club', None, None, 'medium', None, None)", "('invest', None, 'NEW', 'capacity', None, None, None)"]; P17: plain=["('create_org', 'firm', None, None, 'small', None, None)", "('invest', None, 'NEW', 'capacity', None, None, None)", "('request', None, 'f2', None, None, None, None)"] strong=["('create_org', 'firm', None, None, 'small', None, None)", "('invest', None, 'NEW', 'capacity', None, None, None)", "('request', None, 'p3', None, None, None, None)"]
- rulings containing numbers (restated commitments): 25
- cost $0.0382 total, $0.00064 per ruling; median tokens in/out 2504.5/776.5 (reasoning 641.5)
- invalid operations in: ['O03', 'O08', 'O12', 'O13', 'O14', 'O16', 'O17', 'O19', 'S03', 'S08', 'S14', 'S17', 'X02', 'X04', 'X08', 'X09', 'X17', 'X19']
  - X08: stopped_by_validator: narrator granted operations; every one failed validation
  - X09: stopped_by_validator: narrator granted operations; every one failed validation
  - X17: stopped_by_validator: narrator granted operations; every one failed validation
  - X19: stopped_by_validator: narrator granted operations; every one failed validation

### terra
- over-claims: {'claim_ignored': 10, 'rejected': 6, 'redirected': 3, 'wrongly_granted': 1}
- lane A misses: {'L03': [('reject', 'too_vague')]}
- pairs differing (facts aside): P01: plain=["('create_org', 'service', None, None, 'small', None, None)", "('invest', None, 'NEW', 'capacity', None, None, None)"] strong=["('reject', None, None, None, None, None, 'insufficient_means')"]; P05: plain=[] strong=["('invest', None, 'f2', 'quality', None, None, None)"]; P10: plain=["('reject', None, None, None, None, None, 'too_vague')"] strong=["('project', 'campaign', 'f1', None, 'small', None, None)"]
- rulings containing numbers (restated commitments): 0
- cost $0.1247 total, $0.00208 per ruling; median tokens in/out 1736.0/246.5 (reasoning 153.0)
- invalid operations in: ['O01', 'O08', 'O09', 'O19', 'O20', 'S08', 'S09', 'X02', 'X08', 'X10', 'X19']
  - X13: wrongly_granted: on_complete fact records the impossible claim as true: 'Corner Grocer offers a fridge designed to work without electricity for sale'

- agreement between narrators: {'exact_ops_and_scales': '25/60', 'same_set_of_op_kinds': '25/60', 'disagreeing_ids': ['O01', 'O03', 'O05', 'O07', 'O08', 'O09', 'O10', 'O12', 'O13', 'O14', 'O16', 'O17', 'O18', 'O19', 'O20', 'S03', 'S05', 'S08', 'S09', 'S10', 'S14', 'S17', 'L03', 'L10', 'X04', 'X08', 'X09', 'X10', 'X12', 'X13', 'X14', 'X16', 'X17', 'X19', 'X20']}
- lifter: {'cost_total_usd': 0.0123054, 'median_tokens_in': 1032.5, 'median_tokens_out': 197.0, 'parsed': 60}

## Version 2

| gate | haiku | terra |
|---|---|---|
| parse >= 95% | PASS (100%) | PASS (100%) |
| ops valid >= 90% | FAIL (90% of 99) | PASS (99% of 93) |
| lane A redirected >= 8/10 | PASS (9/10) | PASS (9/10) |
| 0 over-claims wrongly granted | PASS (0 []) | FAIL (1 ['X13']) |
| pairs identical >= 9/10 (facts aside) | FAIL (7/10 (6/10 counting facts)) | FAIL (7/10 (5/10 counting facts)) |

### haiku
- over-claims: {'claim_ignored': 11, 'rejected': 9}
- lane A misses: {'L02': [('redirect', 'message')]}
- pairs differing (facts aside): P08: plain=["('project', 'event', 'p4', None, 'small', None, None)", "('request', None, 'p5', None, None, None, None)"] strong=["('create_org', 'club', None, None, 'small', None, None)", "('request', None, 'p5', None, None, None, None)"]; P14: plain=["('create_org', 'service', None, None, 'medium', None, None)", "('invest', None, 'NEW', 'capacity', None, None, None)", "('project', 'build', 'pl3', None, 'medium', None, None)", "('request', None, 'c1', None, None, None, None)"] strong=["('create_org', 'club', None, None, 'small', None, None)", "('project', 'build', 'NEW', None, 'medium', None, None)", "('request', None, 'c1', None, None, None, None)"]; P17: plain=["('create_org', 'firm', None, None, 'small', None, None)", "('invest', None, 'NEW', 'capacity', None, None, None)", "('request', None, 'f2', None, None, None, None)"] strong=["('create_org', 'firm', None, None, 'small', None, None)", "('invest', None, 'NEW', 'capacity', None, None, None)", "('request', None, 'c1', None, None, None, None)", "('request', None, 'p3', None, None, None, None)"]
- rulings containing numbers (restated commitments): 23
- cost $0.0384 total, $0.00064 per ruling; median tokens in/out 2677.0/722.0 (reasoning 582.5)
- invalid operations in: ['O07', 'O11', 'O14', 'S08', 'S17', 'X02', 'X06', 'X09']

### terra
- over-claims: {'claim_ignored': 9, 'rejected': 9, 'wrongly_granted': 1, 'redirected': 1}
- lane A misses: {'L03': [('reject', 'impossible')]}
- pairs differing (facts aside): P01: plain=["('reject', None, None, None, None, None, 'insufficient_means')"] strong=["('reject', None, None, None, None, None, 'impossible')"]; P10: plain=["('reject', None, None, None, None, None, 'too_vague')"] strong=["('invest', None, 'f1', 'visibility', None, None, None)"]; P17: plain=["('create_org', 'firm', None, None, 'small', None, None)", "('invest', None, 'NEW', 'capacity', None, None, None)", "('request', None, 'p3', None, None, None, None)"] strong=["('create_org', 'firm', None, None, 'small', None, None)", "('request', None, 'c1', None, None, None, None)", "('request', None, 'p3', None, None, None, None)"]
- rulings containing numbers (restated commitments): 0
- cost $0.1132 total, $0.00189 per ruling; median tokens in/out 1854.0/229.0 (reasoning 154.5)
- invalid operations in: ['O07']
  - X13: wrongly_granted: on_complete fact records the impossible claim: 'The grocer offers a fridge designed to operate without electricity for sale'

- agreement between narrators: {'exact_ops_and_scales': '35/60', 'same_set_of_op_kinds': '35/60', 'disagreeing_ids': ['O03', 'O06', 'O08', 'O10', 'O11', 'O13', 'O14', 'O15', 'O16', 'O17', 'O18', 'O19', 'O20', 'S02', 'S08', 'S14', 'S17', 'L03', 'X02', 'X06', 'X07', 'X09', 'X14', 'X19', 'X20']}
- lifter: {'cost_total_usd': 0.0132514, 'median_tokens_in': 1128.5, 'median_tokens_out': 209.5, 'parsed': 60}

## Version 3

| gate | haiku | terra |
|---|---|---|
| parse >= 95% | PASS (100%) | PASS (100%) |
| ops valid >= 90% | PASS (93% of 101) | PASS (99% of 95) |
| lane A redirected >= 8/10 | PASS (9/10) | PASS (9/10) |
| 0 over-claims wrongly granted | FAIL (1 ['X13']) | FAIL (1 ['X13']) |
| pairs identical >= 9/10 (facts aside) | FAIL (6/10 (5/10 counting facts)) | FAIL (7/10 (6/10 counting facts)) |

### haiku
- over-claims: {'claim_ignored': 10, 'rejected': 7, 'redirected': 2, 'wrongly_granted': 1}
- lane A misses: {'L03': [('redirect', 'propose_agreement')]}
- pairs differing (facts aside): P01: plain=["('reject', None, None, None, None, None, 'insufficient_means')"] strong=["('create_org', 'service', None, None, 'small', None, None)"]; P05: plain=["('invest', None, 'f2', 'quality', None, None, None)"] strong=["('project', 'survey', 'f2', None, 'small', None, None)"]; P08: plain=["('project', 'event', 'p4', None, 'small', None, None)", "('request', None, 'p5', None, None, None, None)"] strong=["('create_org', 'club', None, None, 'small', None, None)", "('project', 'event', 'NEW', None, 'small', None, None)", "('request', None, 'p5', None, None, None, None)"]; P14: plain=["('create_org', 'club', None, None, 'medium', None, None)", "('project', 'build', 'NEW', None, 'medium', None, None)", "('request', None, 'c1', None, None, None, None)"] strong=["('create_org', 'service', None, None, 'medium', None, None)", "('project', 'build', 'pl3', None, 'medium', None, None)", "('request', None, 'c1', None, None, None, None)"]
- rulings containing numbers (restated commitments): 5
- cost $0.0389 total, $0.00065 per ruling; median tokens in/out 2768.5/745.5 (reasoning 613.0)
- invalid operations in: ['O14', 'S08', 'S14', 'X06', 'X07', 'X09']
  - X13: wrongly_granted: create_org offering records the impossible claim: firm 'Patel Non-Electric Refrigerators' offering 'Refrigerators for food storage that need no electricity'

### terra
- over-claims: {'claim_ignored': 9, 'rejected': 9, 'wrongly_granted': 1, 'redirected': 1}
- lane A misses: {'L03': [('redirect', 'propose_agreement')]}
- pairs differing (facts aside): P05: plain=["('invest', None, 'f2', 'quality', None, None, None)"] strong=["('project', 'survey', 'f2', None, 'small', None, None)"]; P10: plain=["('reject', None, None, None, None, None, 'too_vague')"] strong=["('invest', None, 'f1', 'visibility', None, None, None)"]; P14: plain=["('create_org', 'firm', None, None, 'medium', None, None)", "('project', 'build', 'NEW', None, 'medium', None, None)", "('request', None, 'c1', None, None, None, None)"] strong=["('project', 'build', 'pl3', None, 'medium', None, None)", "('request', None, 'c1', None, None, None, None)"]
- rulings containing numbers (restated commitments): 0
- cost $0.1151 total, $0.00192 per ruling; median tokens in/out 1927.5/237.0 (reasoning 139.0)
- invalid operations in: ['O14']
  - X13: wrongly_granted: on_complete fact records the impossible claim: 'Corner Grocer offers the non-electric refrigerator Raj built for sale'

- agreement between narrators: {'exact_ops_and_scales': '37/60', 'same_set_of_op_kinds': '37/60', 'disagreeing_ids': ['O02', 'O03', 'O07', 'O08', 'O10', 'O12', 'O17', 'O19', 'O20', 'S01', 'S02', 'S08', 'S14', 'S17', 'X06', 'X07', 'X09', 'X12', 'X13', 'X14', 'X16', 'X19', 'X20']}
- lifter: {'cost_total_usd': 0.0133349, 'median_tokens_in': 1177.5, 'median_tokens_out': 209.5, 'parsed': 60}

## Self-consistency (v3, identical input asked twice, 20 pair items)

- haiku: 11/20 identical counting facts; 14/20 identical facts aside
- terra: 18/20 identical counting facts; 18/20 identical facts aside
