# Expected price path walk-forward (large500)

Verdict (preregistered section 6.7): **FAIL** -- S2 fails: recorded in docs; not shown in the table.
Failed checks: S1_h3_below_analyst_and_engine, S1_h5_below_analyst_and_engine, S1_h1_not_worse_than_mechanical_by_0.03, S1_h3_not_worse_than_mechanical_by_0.03, S1_h5_not_worse_than_mechanical_by_0.03, S2_h1_bias_within_0.10, S2_h3_bias_within_0.10, S2_h5_bias_within_0.10, S3_h3_band, S3_h5_band

## Horizon 1 year(s): formations 2016-2025

Rows in range 4199; excluded (independent counts): no lane path 912, no realized price 0, no engine base_irr 305, no trailing 3-year EPS CAGR 466; common sample n = 2600, lane-only sample n = 3287.
Exclusion reasons (no lane path): TTM net income not positive 554, no point-in-time share count 233, TTM net income missing 143, normalized (mid-cycle) net margin not positive 77, no positive TTM revenue 3

| predictor (common sample) | n | median log error (bias) | mean log error | median abs log error | within +-25% | share above actual |
|---|---|---|---|---|---|---|
| lane_base | 2600 | -0.113 | -0.125 | 0.213 | 53% | 34% |
| random_walk | 2600 | -0.120 | -0.119 | 0.195 | 56% | 32% |
| drift_7 | 2600 | -0.053 | -0.051 | 0.173 | 61% | 42% |
| analyst_const | 2600 | -0.030 | -0.019 | 0.190 | 57% | 46% |
| engine_irr | 2600 | -0.065 | -0.065 | 0.175 | 60% | 40% |
| lane_bear | 2600 | -0.524 | -0.575 | 0.532 | 16% | 7% |
| lane_bull | 2600 | 0.199 | 0.230 | 0.269 | 43% | 74% |
| engine_irr_total | 2600 | -0.049 | -0.051 | 0.173 | 61% | 42% |

Band: realized within [bear, bull] 67%, below bear 7%, above bull 26% (n 2600).
Head to head (share of rows where the lane's |log error| is smaller): vs random_walk 46%, vs drift_7 39%, vs analyst_const 45%, vs engine_irr 39%
Yearly Spearman of pred / P_0 vs realized ratio (mean, t over formation years): lane_base 0.058 (t 1.5, 10 yrs), random_walk -0.002 (t -0.1, 10 yrs), drift_7 -0.003 (t -0.1, 10 yrs), analyst_const 0.048 (t 1.4, 10 yrs), engine_irr 0.085 (t 3.0, 10 yrs)

Survivorship-deflated (6.8; per-window rescale to SPY, n 2600): lane_base bias -0.122 MdALE 0.221 within25 50%; random_walk bias -0.130 MdALE 0.204 within25 55%; drift_7 bias -0.062 MdALE 0.176 within25 60%; analyst_const bias -0.039 MdALE 0.190 within25 56%; engine_irr bias -0.075 MdALE 0.179 within25 58%; lane_bear bias -0.532 MdALE 0.542 within25 16%; lane_bull bias 0.189 MdALE 0.269 within25 44%; band within 66% below 7% above 27%

By formation year (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| 2016 | 205 | -0.202 | 0.222 | 50% | 0.193 | 0.154 | 0.184 | 0.167 |
| 2017 | 219 | -0.139 | 0.197 | 54% | 0.178 | 0.151 | 0.172 | 0.155 |
| 2018 | 227 | -0.077 | 0.219 | 52% | 0.163 | 0.143 | 0.158 | 0.151 |
| 2019 | 263 | -0.025 | 0.192 | 59% | 0.177 | 0.173 | 0.197 | 0.164 |
| 2020 | 265 | -0.293 | 0.297 | 36% | 0.306 | 0.238 | 0.222 | 0.269 |
| 2021 | 256 | 0.082 | 0.186 | 60% | 0.172 | 0.203 | 0.208 | 0.192 |
| 2022 | 293 | -0.061 | 0.179 | 59% | 0.184 | 0.150 | 0.185 | 0.147 |
| 2023 | 295 | -0.162 | 0.212 | 53% | 0.199 | 0.164 | 0.157 | 0.167 |
| 2024 | 265 | -0.098 | 0.194 | 57% | 0.168 | 0.152 | 0.176 | 0.152 |
| 2025 | 312 | -0.123 | 0.223 | 50% | 0.250 | 0.217 | 0.239 | 0.225 |

By stage (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| decline | 86 | -0.192 | 0.242 | 43% | 0.196 | 0.156 | 0.208 | 0.180 |
| early | 1 | -2.258 | 2.258 | 0% | 0.111 | 0.044 | 0.151 | 0.073 |
| growth | 345 | -0.173 | 0.286 | 41% | 0.241 | 0.220 | 0.233 | 0.220 |
| mature | 1299 | -0.088 | 0.193 | 57% | 0.181 | 0.163 | 0.180 | 0.164 |
| maturing | 869 | -0.125 | 0.214 | 53% | 0.198 | 0.174 | 0.192 | 0.176 |

By cyclical flag (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| False | 1352 | -0.123 | 0.200 | 55% | 0.184 | 0.162 | 0.178 | 0.164 |
| True | 1248 | -0.095 | 0.221 | 51% | 0.206 | 0.184 | 0.204 | 0.196 |

By multiple today (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| 10-15x | 243 | -0.045 | 0.162 | 62% | 0.194 | 0.164 | 0.191 | 0.161 |
| 15-20x | 395 | -0.102 | 0.188 | 60% | 0.174 | 0.158 | 0.166 | 0.161 |
| 20-30x | 840 | -0.109 | 0.188 | 60% | 0.178 | 0.153 | 0.169 | 0.155 |
| 30-60x | 772 | -0.129 | 0.221 | 50% | 0.194 | 0.178 | 0.195 | 0.181 |
| <10x | 113 | 0.128 | 0.277 | 40% | 0.246 | 0.217 | 0.231 | 0.206 |
| >60x (capped) | 237 | -0.457 | 0.494 | 23% | 0.294 | 0.274 | 0.274 | 0.289 |

By size class (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| $0-325m | 4 | -0.650 | 0.811 | 0% | 0.243 | 0.243 | 0.229 | 0.301 |
| $1,250-2,000m | 140 | -0.195 | 0.261 | 44% | 0.244 | 0.226 | 0.213 | 0.222 |
| $12,000-25,000m | 526 | -0.078 | 0.199 | 56% | 0.183 | 0.172 | 0.171 | 0.173 |
| $2,000-3,000m | 238 | -0.193 | 0.228 | 49% | 0.223 | 0.199 | 0.202 | 0.213 |
| $3,000-4,500m | 263 | -0.109 | 0.193 | 55% | 0.167 | 0.161 | 0.177 | 0.157 |
| $325-700m | 40 | -0.376 | 0.420 | 18% | 0.315 | 0.269 | 0.260 | 0.284 |
| $4,500-7,000m | 316 | -0.121 | 0.197 | 56% | 0.197 | 0.164 | 0.192 | 0.169 |
| $7,000-12,000m | 413 | -0.120 | 0.213 | 54% | 0.193 | 0.163 | 0.186 | 0.168 |
| $700-1,250m | 71 | -0.406 | 0.406 | 24% | 0.313 | 0.289 | 0.239 | 0.284 |
| >$25,000m | 288 | -0.048 | 0.217 | 52% | 0.193 | 0.165 | 0.210 | 0.166 |
| >$50,000m | 301 | -0.051 | 0.165 | 62% | 0.153 | 0.138 | 0.176 | 0.139 |

Lane-only sample (lane rows with a realized price; no engine or EPS requirement):

| predictor | n | bias | MdALE | within25 |
|---|---|---|---|---|
| lane_base | 3287 | -0.115 | 0.220 | 51% |
| lane_bear | 3287 | -0.532 | 0.551 | 16% |
| lane_bull | 3287 | 0.201 | 0.291 | 41% |
| random_walk | 3287 | -0.117 | 0.195 | 56% |
| drift_7 | 3287 | -0.049 | 0.174 | 60% |

Lane-only band: within 65%, below bear 8%, above bull 27%; head to head vs random_walk 44%, vs drift_7 38%

## Horizon 2 year(s): formations 2016-2024

Rows in range 3722; excluded (independent counts): no lane path 830, no realized price 0, no engine base_irr 259, no trailing 3-year EPS CAGR 420; common sample n = 2288, lane-only sample n = 2892.
Exclusion reasons (no lane path): TTM net income not positive 504, no point-in-time share count 204, TTM net income missing 140, normalized (mid-cycle) net margin not positive 67, no positive TTM revenue 3

| predictor (common sample) | n | median log error (bias) | mean log error | median abs log error | within +-25% | share above actual |
|---|---|---|---|---|---|---|
| lane_base | 2288 | -0.211 | -0.236 | 0.300 | 39% | 27% |
| random_walk | 2288 | -0.216 | -0.221 | 0.272 | 42% | 24% |
| drift_7 | 2288 | -0.081 | -0.086 | 0.222 | 50% | 40% |
| analyst_const | 2288 | -0.021 | -0.015 | 0.269 | 42% | 48% |
| engine_irr | 2288 | -0.114 | -0.114 | 0.228 | 49% | 37% |
| lane_bear | 2288 | -0.731 | -0.779 | 0.737 | 9% | 6% |
| lane_bull | 2288 | 0.159 | 0.184 | 0.297 | 39% | 67% |
| engine_irr_total | 2288 | -0.084 | -0.087 | 0.223 | 50% | 40% |

Band: realized within [bear, bull] 61%, below bear 6%, above bull 33% (n 2288).
Head to head (share of rows where the lane's |log error| is smaller): vs random_walk 46%, vs drift_7 35%, vs analyst_const 46%, vs engine_irr 35%
Yearly Spearman of pred / P_0 vs realized ratio (mean, t over formation years): lane_base 0.091 (t 2.5, 9 yrs), random_walk -0.011 (t -0.5, 9 yrs), drift_7 -0.012 (t -0.7, 9 yrs), analyst_const 0.054 (t 1.6, 9 yrs), engine_irr 0.091 (t 2.5, 9 yrs)

Survivorship-deflated (6.8; per-window rescale to SPY, n 2288): lane_base bias -0.246 MdALE 0.315 within25 37%; random_walk bias -0.244 MdALE 0.291 within25 39%; drift_7 bias -0.109 MdALE 0.235 within25 48%; analyst_const bias -0.046 MdALE 0.264 within25 42%; engine_irr bias -0.142 MdALE 0.239 within25 47%; lane_bear bias -0.755 MdALE 0.767 within25 8%; lane_bull bias 0.127 MdALE 0.290 within25 40%; band within 57% below 5% above 38%

By formation year (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| 2016 | 205 | -0.352 | 0.377 | 29% | 0.352 | 0.252 | 0.271 | 0.288 |
| 2017 | 219 | -0.249 | 0.317 | 36% | 0.280 | 0.214 | 0.292 | 0.240 |
| 2018 | 227 | -0.134 | 0.309 | 37% | 0.235 | 0.228 | 0.297 | 0.217 |
| 2019 | 263 | -0.334 | 0.353 | 30% | 0.355 | 0.252 | 0.222 | 0.251 |
| 2020 | 265 | -0.193 | 0.256 | 45% | 0.253 | 0.185 | 0.219 | 0.200 |
| 2021 | 256 | -0.051 | 0.213 | 52% | 0.179 | 0.191 | 0.272 | 0.180 |
| 2022 | 293 | -0.114 | 0.254 | 45% | 0.307 | 0.241 | 0.287 | 0.237 |
| 2023 | 295 | -0.245 | 0.338 | 38% | 0.287 | 0.222 | 0.304 | 0.231 |
| 2024 | 265 | -0.211 | 0.302 | 39% | 0.269 | 0.248 | 0.296 | 0.238 |

By stage (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| decline | 74 | -0.307 | 0.390 | 32% | 0.245 | 0.233 | 0.203 | 0.229 |
| early | 1 | -2.797 | 2.797 | 0% | 0.538 | 0.403 | 0.013 | 0.462 |
| growth | 308 | -0.306 | 0.403 | 31% | 0.363 | 0.297 | 0.337 | 0.311 |
| mature | 1148 | -0.201 | 0.275 | 41% | 0.253 | 0.207 | 0.256 | 0.208 |
| maturing | 757 | -0.206 | 0.302 | 41% | 0.269 | 0.224 | 0.279 | 0.228 |

By cyclical flag (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| False | 1211 | -0.205 | 0.290 | 40% | 0.261 | 0.218 | 0.258 | 0.216 |
| True | 1077 | -0.222 | 0.310 | 38% | 0.293 | 0.234 | 0.292 | 0.243 |

By multiple today (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| 10-15x | 216 | -0.018 | 0.214 | 51% | 0.295 | 0.253 | 0.277 | 0.243 |
| 15-20x | 350 | -0.163 | 0.232 | 49% | 0.261 | 0.214 | 0.237 | 0.206 |
| 20-30x | 749 | -0.210 | 0.265 | 43% | 0.264 | 0.210 | 0.247 | 0.218 |
| 30-60x | 670 | -0.275 | 0.316 | 36% | 0.260 | 0.219 | 0.287 | 0.228 |
| <10x | 100 | 0.267 | 0.405 | 31% | 0.329 | 0.257 | 0.351 | 0.233 |
| >60x (capped) | 203 | -0.748 | 0.755 | 14% | 0.363 | 0.295 | 0.397 | 0.306 |

By size class (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| $0-325m | 4 | -1.076 | 1.251 | 0% | 0.766 | 0.631 | 0.720 | 0.679 |
| $1,250-2,000m | 127 | -0.330 | 0.399 | 37% | 0.358 | 0.270 | 0.344 | 0.298 |
| $12,000-25,000m | 466 | -0.166 | 0.253 | 44% | 0.248 | 0.200 | 0.239 | 0.204 |
| $2,000-3,000m | 209 | -0.258 | 0.322 | 37% | 0.311 | 0.247 | 0.274 | 0.261 |
| $3,000-4,500m | 233 | -0.257 | 0.290 | 41% | 0.251 | 0.197 | 0.256 | 0.183 |
| $325-700m | 37 | -0.753 | 0.753 | 11% | 0.402 | 0.347 | 0.401 | 0.354 |
| $4,500-7,000m | 272 | -0.250 | 0.288 | 38% | 0.283 | 0.218 | 0.255 | 0.227 |
| $7,000-12,000m | 363 | -0.254 | 0.335 | 36% | 0.293 | 0.225 | 0.285 | 0.246 |
| $700-1,250m | 66 | -0.585 | 0.585 | 15% | 0.476 | 0.351 | 0.385 | 0.398 |
| >$25,000m | 247 | -0.098 | 0.286 | 44% | 0.243 | 0.214 | 0.293 | 0.216 |
| >$50,000m | 264 | -0.082 | 0.246 | 45% | 0.243 | 0.223 | 0.263 | 0.202 |

Lane-only sample (lane rows with a realized price; no engine or EPS requirement):

| predictor | n | bias | MdALE | within25 |
|---|---|---|---|---|
| lane_base | 2892 | -0.221 | 0.320 | 37% |
| lane_bear | 2892 | -0.748 | 0.770 | 9% |
| lane_bull | 2892 | 0.159 | 0.316 | 38% |
| random_walk | 2892 | -0.217 | 0.279 | 41% |
| drift_7 | 2892 | -0.081 | 0.225 | 50% |

Lane-only band: within 59%, below bear 7%, above bull 34%; head to head vs random_walk 44%, vs drift_7 34%

## Horizon 3 year(s): formations 2016-2023

Rows in range 3257; excluded (independent counts): no lane path 743, no realized price 0, no engine base_irr 218, no trailing 3-year EPS CAGR 333; common sample n = 2023, lane-only sample n = 2514.
Exclusion reasons (no lane path): TTM net income not positive 448, no point-in-time share count 177, TTM net income missing 136, normalized (mid-cycle) net margin not positive 56, no positive TTM revenue 3

| predictor (common sample) | n | median log error (bias) | mean log error | median abs log error | within +-25% | share above actual |
|---|---|---|---|---|---|---|
| lane_base | 2023 | -0.311 | -0.336 | 0.399 | 29% | 24% |
| random_walk | 2023 | -0.311 | -0.325 | 0.362 | 31% | 20% |
| drift_7 | 2023 | -0.108 | -0.122 | 0.278 | 43% | 39% |
| analyst_const | 2023 | -0.013 | -0.025 | 0.374 | 32% | 49% |
| engine_irr | 2023 | -0.163 | -0.165 | 0.286 | 41% | 33% |
| lane_bear | 2023 | -0.885 | -0.946 | 0.900 | 7% | 7% |
| lane_bull | 2023 | 0.123 | 0.135 | 0.326 | 37% | 60% |
| engine_irr_total | 2023 | -0.114 | -0.124 | 0.278 | 42% | 37% |

Band: realized within [bear, bull] 54%, below bear 7%, above bull 40% (n 2023).
Head to head (share of rows where the lane's |log error| is smaller): vs random_walk 44%, vs drift_7 33%, vs analyst_const 47%, vs engine_irr 34%
Yearly Spearman of pred / P_0 vs realized ratio (mean, t over formation years): lane_base 0.135 (t 5.0, 8 yrs), random_walk 0.011 (t 0.5, 8 yrs), drift_7 0.019 (t 0.8, 8 yrs), analyst_const 0.075 (t 2.3, 8 yrs), engine_irr 0.115 (t 2.9, 8 yrs)

Survivorship-deflated (6.8; per-window rescale to SPY, n 2023): lane_base bias -0.362 MdALE 0.428 within25 26%; random_walk bias -0.347 MdALE 0.398 within25 28%; drift_7 bias -0.144 MdALE 0.281 within25 41%; analyst_const bias -0.059 MdALE 0.368 within25 31%; engine_irr bias -0.204 MdALE 0.300 within25 38%; lane_bear bias -0.935 MdALE 0.945 within25 6%; lane_bull bias 0.074 MdALE 0.318 within25 38%; band within 50% below 6% above 44%

By formation year (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| 2016 | 205 | -0.480 | 0.506 | 22% | 0.465 | 0.313 | 0.395 | 0.361 |
| 2017 | 219 | -0.294 | 0.418 | 30% | 0.352 | 0.292 | 0.380 | 0.318 |
| 2018 | 227 | -0.437 | 0.484 | 24% | 0.469 | 0.296 | 0.377 | 0.320 |
| 2019 | 263 | -0.301 | 0.346 | 29% | 0.286 | 0.199 | 0.361 | 0.208 |
| 2020 | 265 | -0.329 | 0.410 | 27% | 0.372 | 0.240 | 0.274 | 0.258 |
| 2021 | 256 | -0.204 | 0.312 | 32% | 0.276 | 0.230 | 0.390 | 0.232 |
| 2022 | 293 | -0.168 | 0.353 | 34% | 0.413 | 0.322 | 0.417 | 0.325 |
| 2023 | 295 | -0.349 | 0.424 | 28% | 0.347 | 0.296 | 0.433 | 0.300 |

By stage (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| decline | 69 | -0.378 | 0.457 | 16% | 0.298 | 0.253 | 0.399 | 0.258 |
| early | 1 | -2.129 | 2.129 | 0% | 0.251 | 0.454 | 1.038 | 0.364 |
| growth | 262 | -0.412 | 0.519 | 19% | 0.461 | 0.381 | 0.430 | 0.384 |
| mature | 1067 | -0.283 | 0.382 | 32% | 0.334 | 0.252 | 0.368 | 0.266 |
| maturing | 624 | -0.315 | 0.393 | 28% | 0.394 | 0.285 | 0.347 | 0.291 |

By cyclical flag (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| False | 1081 | -0.285 | 0.372 | 30% | 0.345 | 0.275 | 0.343 | 0.277 |
| True | 942 | -0.347 | 0.428 | 27% | 0.379 | 0.280 | 0.398 | 0.298 |

By multiple today (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| 10-15x | 193 | -0.005 | 0.270 | 41% | 0.386 | 0.299 | 0.389 | 0.300 |
| 15-20x | 310 | -0.202 | 0.306 | 40% | 0.353 | 0.217 | 0.339 | 0.247 |
| 20-30x | 676 | -0.292 | 0.356 | 32% | 0.343 | 0.273 | 0.325 | 0.276 |
| 30-60x | 570 | -0.444 | 0.461 | 21% | 0.349 | 0.260 | 0.405 | 0.293 |
| <10x | 91 | 0.313 | 0.554 | 26% | 0.407 | 0.340 | 0.410 | 0.272 |
| >60x (capped) | 183 | -1.010 | 1.011 | 9% | 0.460 | 0.372 | 0.536 | 0.364 |

By size class (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| $0-325m | 4 | -1.259 | 1.309 | 0% | 0.830 | 0.627 | 0.611 | 0.710 |
| $1,250-2,000m | 112 | -0.395 | 0.452 | 27% | 0.491 | 0.317 | 0.385 | 0.358 |
| $12,000-25,000m | 415 | -0.239 | 0.329 | 34% | 0.330 | 0.238 | 0.338 | 0.267 |
| $2,000-3,000m | 186 | -0.388 | 0.429 | 23% | 0.412 | 0.302 | 0.409 | 0.336 |
| $3,000-4,500m | 206 | -0.381 | 0.411 | 29% | 0.363 | 0.252 | 0.367 | 0.271 |
| $325-700m | 34 | -0.810 | 0.810 | 6% | 0.490 | 0.335 | 0.450 | 0.415 |
| $4,500-7,000m | 237 | -0.341 | 0.393 | 27% | 0.365 | 0.270 | 0.305 | 0.265 |
| $7,000-12,000m | 321 | -0.388 | 0.427 | 27% | 0.352 | 0.283 | 0.370 | 0.288 |
| $700-1,250m | 59 | -0.801 | 0.801 | 5% | 0.667 | 0.464 | 0.400 | 0.471 |
| >$25,000m | 213 | -0.136 | 0.377 | 34% | 0.332 | 0.262 | 0.475 | 0.231 |
| >$50,000m | 236 | -0.145 | 0.340 | 34% | 0.326 | 0.271 | 0.370 | 0.279 |

Lane-only sample (lane rows with a realized price; no engine or EPS requirement):

| predictor | n | bias | MdALE | within25 |
|---|---|---|---|---|
| lane_base | 2514 | -0.330 | 0.418 | 27% |
| lane_bear | 2514 | -0.915 | 0.935 | 6% |
| lane_bull | 2514 | 0.123 | 0.342 | 35% |
| random_walk | 2514 | -0.316 | 0.364 | 31% |
| drift_7 | 2514 | -0.113 | 0.282 | 42% |

Lane-only band: within 53%, below bear 7%, above bull 40%; head to head vs random_walk 43%, vs drift_7 33%

## Horizon 4 year(s): formations 2016-2022

Rows in range 2801; excluded (independent counts): no lane path 647, no realized price 0, no engine base_irr 190, no trailing 3-year EPS CAGR 290; common sample n = 1728, lane-only sample n = 2154.
Exclusion reasons (no lane path): TTM net income not positive 378, no point-in-time share count 150, TTM net income missing 129, normalized (mid-cycle) net margin not positive 48, no positive TTM revenue 3

| predictor (common sample) | n | median log error (bias) | mean log error | median abs log error | within +-25% | share above actual |
|---|---|---|---|---|---|---|
| lane_base | 1728 | -0.400 | -0.400 | 0.492 | 22% | 24% |
| random_walk | 1728 | -0.395 | -0.428 | 0.453 | 26% | 18% |
| drift_7 | 1728 | -0.124 | -0.157 | 0.330 | 38% | 39% |
| analyst_const | 1728 | -0.026 | -0.035 | 0.479 | 25% | 49% |
| engine_irr | 1728 | -0.207 | -0.218 | 0.356 | 34% | 33% |
| lane_bear | 1728 | -0.993 | -1.055 | 1.008 | 7% | 8% |
| lane_bull | 1728 | 0.048 | 0.087 | 0.356 | 34% | 55% |
| engine_irr_total | 1728 | -0.146 | -0.164 | 0.335 | 36% | 38% |

Band: realized within [bear, bull] 47%, below bear 8%, above bull 45% (n 1728).
Head to head (share of rows where the lane's |log error| is smaller): vs random_walk 47%, vs drift_7 32%, vs analyst_const 48%, vs engine_irr 33%
Yearly Spearman of pred / P_0 vs realized ratio (mean, t over formation years): lane_base 0.137 (t 3.4, 7 yrs), random_walk 0.023 (t 1.8, 7 yrs), drift_7 0.015 (t 1.1, 7 yrs), analyst_const 0.078 (t 2.0, 7 yrs), engine_irr 0.124 (t 2.8, 7 yrs)

Survivorship-deflated (6.8; per-window rescale to SPY, n 1728): lane_base bias -0.469 MdALE 0.541 within25 21%; random_walk bias -0.465 MdALE 0.503 within25 21%; drift_7 bias -0.194 MdALE 0.340 within25 35%; analyst_const bias -0.097 MdALE 0.478 within25 24%; engine_irr bias -0.274 MdALE 0.390 within25 32%; lane_bear bias -1.065 MdALE 1.075 within25 6%; lane_bull bias -0.017 MdALE 0.364 within25 32%; band within 42% below 7% above 51%

By formation year (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| 2016 | 205 | -0.428 | 0.525 | 19% | 0.469 | 0.354 | 0.554 | 0.407 |
| 2017 | 219 | -0.648 | 0.657 | 18% | 0.632 | 0.400 | 0.501 | 0.431 |
| 2018 | 227 | -0.381 | 0.441 | 24% | 0.392 | 0.246 | 0.495 | 0.265 |
| 2019 | 263 | -0.430 | 0.476 | 21% | 0.398 | 0.241 | 0.410 | 0.265 |
| 2020 | 265 | -0.391 | 0.511 | 21% | 0.460 | 0.336 | 0.374 | 0.360 |
| 2021 | 256 | -0.278 | 0.436 | 25% | 0.371 | 0.297 | 0.519 | 0.314 |
| 2022 | 293 | -0.241 | 0.461 | 27% | 0.529 | 0.394 | 0.525 | 0.397 |

By stage (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| decline | 66 | -0.447 | 0.650 | 15% | 0.347 | 0.307 | 0.508 | 0.364 |
| early | 1 | -3.264 | 3.264 | 0% | 0.766 | 0.495 | 0.284 | 0.614 |
| growth | 212 | -0.673 | 0.715 | 15% | 0.588 | 0.450 | 0.554 | 0.486 |
| mature | 960 | -0.357 | 0.449 | 25% | 0.410 | 0.308 | 0.471 | 0.312 |
| maturing | 489 | -0.412 | 0.491 | 22% | 0.503 | 0.349 | 0.451 | 0.384 |

By cyclical flag (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| False | 943 | -0.336 | 0.456 | 23% | 0.425 | 0.313 | 0.440 | 0.333 |
| True | 785 | -0.454 | 0.534 | 21% | 0.473 | 0.350 | 0.520 | 0.372 |

By multiple today (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| 10-15x | 165 | -0.093 | 0.343 | 35% | 0.544 | 0.387 | 0.523 | 0.366 |
| 15-20x | 265 | -0.254 | 0.385 | 28% | 0.445 | 0.288 | 0.409 | 0.336 |
| 20-30x | 586 | -0.338 | 0.419 | 26% | 0.432 | 0.324 | 0.438 | 0.345 |
| 30-60x | 482 | -0.565 | 0.587 | 17% | 0.444 | 0.306 | 0.488 | 0.354 |
| <10x | 74 | 0.511 | 0.642 | 11% | 0.393 | 0.319 | 0.546 | 0.368 |
| >60x (capped) | 156 | -1.087 | 1.132 | 6% | 0.514 | 0.435 | 0.661 | 0.443 |

By size class (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| $0-325m | 4 | -1.736 | 1.736 | 0% | 1.256 | 0.985 | 0.896 | 1.095 |
| $1,250-2,000m | 98 | -0.518 | 0.577 | 22% | 0.597 | 0.396 | 0.569 | 0.439 |
| $12,000-25,000m | 348 | -0.257 | 0.418 | 27% | 0.411 | 0.272 | 0.400 | 0.306 |
| $2,000-3,000m | 164 | -0.481 | 0.546 | 20% | 0.522 | 0.334 | 0.492 | 0.389 |
| $3,000-4,500m | 173 | -0.444 | 0.504 | 23% | 0.445 | 0.311 | 0.413 | 0.331 |
| $325-700m | 31 | -0.989 | 0.989 | 6% | 0.673 | 0.495 | 0.416 | 0.535 |
| $4,500-7,000m | 205 | -0.447 | 0.481 | 19% | 0.485 | 0.321 | 0.468 | 0.348 |
| $7,000-12,000m | 275 | -0.478 | 0.574 | 18% | 0.445 | 0.358 | 0.519 | 0.356 |
| $700-1,250m | 52 | -0.873 | 0.873 | 8% | 0.878 | 0.607 | 0.488 | 0.595 |
| >$25,000m | 175 | -0.186 | 0.419 | 26% | 0.411 | 0.301 | 0.534 | 0.294 |
| >$50,000m | 203 | -0.170 | 0.435 | 28% | 0.381 | 0.320 | 0.474 | 0.355 |

Lane-only sample (lane rows with a realized price; no engine or EPS requirement):

| predictor | n | bias | MdALE | within25 |
|---|---|---|---|---|
| lane_base | 2154 | -0.413 | 0.521 | 22% |
| lane_bear | 2154 | -1.031 | 1.073 | 6% |
| lane_bull | 2154 | 0.050 | 0.372 | 33% |
| random_walk | 2154 | -0.396 | 0.457 | 27% |
| drift_7 | 2154 | -0.126 | 0.335 | 36% |

Lane-only band: within 47%, below bear 8%, above bull 45%; head to head vs random_walk 45%, vs drift_7 32%

## Horizon 5 year(s): formations 2016-2021

Rows in range 2349; excluded (independent counts): no lane path 558, no realized price 0, no engine base_irr 155, no trailing 3-year EPS CAGR 245; common sample n = 1435, lane-only sample n = 1791.
Exclusion reasons (no lane path): TTM net income not positive 320, no point-in-time share count 124, TTM net income missing 121, normalized (mid-cycle) net margin not positive 39, no positive TTM revenue 3

| predictor (common sample) | n | median log error (bias) | mean log error | median abs log error | within +-25% | share above actual |
|---|---|---|---|---|---|---|
| lane_base | 1435 | -0.504 | -0.510 | 0.588 | 18% | 21% |
| random_walk | 1435 | -0.501 | -0.522 | 0.557 | 20% | 16% |
| drift_7 | 1435 | -0.162 | -0.184 | 0.363 | 32% | 38% |
| analyst_const | 1435 | -0.023 | -0.042 | 0.577 | 21% | 49% |
| engine_irr | 1435 | -0.270 | -0.273 | 0.408 | 29% | 30% |
| lane_bear | 1435 | -1.124 | -1.187 | 1.143 | 7% | 8% |
| lane_bull | 1435 | -0.028 | -0.007 | 0.381 | 31% | 48% |
| engine_irr_total | 1435 | -0.202 | -0.205 | 0.386 | 31% | 36% |

Band: realized within [bear, bull] 40%, below bear 8%, above bull 52% (n 1435).
Head to head (share of rows where the lane's |log error| is smaller): vs random_walk 47%, vs drift_7 30%, vs analyst_const 49%, vs engine_irr 31%
Yearly Spearman of pred / P_0 vs realized ratio (mean, t over formation years): lane_base 0.148 (t 5.4, 6 yrs), random_walk 0.024 (t 1.7, 6 yrs), drift_7 0.026 (t 0.9, 6 yrs), analyst_const 0.077 (t 1.8, 6 yrs), engine_irr 0.144 (t 3.7, 6 yrs)

Survivorship-deflated (6.8; per-window rescale to SPY, n 1435): lane_base bias -0.582 MdALE 0.647 within25 15%; random_walk bias -0.587 MdALE 0.613 within25 17%; drift_7 bias -0.249 MdALE 0.387 within25 31%; analyst_const bias -0.094 MdALE 0.557 within25 22%; engine_irr bias -0.348 MdALE 0.445 within25 26%; lane_bear bias -1.194 MdALE 1.206 within25 6%; lane_bull bias -0.111 MdALE 0.394 within25 29%; band within 35% below 7% above 58%

By formation year (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| 2016 | 205 | -0.737 | 0.763 | 11% | 0.819 | 0.524 | 0.648 | 0.588 |
| 2017 | 219 | -0.493 | 0.550 | 16% | 0.531 | 0.308 | 0.576 | 0.352 |
| 2018 | 227 | -0.462 | 0.544 | 18% | 0.515 | 0.326 | 0.560 | 0.333 |
| 2019 | 263 | -0.534 | 0.583 | 19% | 0.534 | 0.334 | 0.493 | 0.389 |
| 2020 | 265 | -0.449 | 0.560 | 20% | 0.561 | 0.405 | 0.421 | 0.447 |
| 2021 | 256 | -0.341 | 0.578 | 21% | 0.483 | 0.396 | 0.701 | 0.413 |

By stage (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| decline | 64 | -0.606 | 0.698 | 16% | 0.430 | 0.335 | 0.596 | 0.313 |
| early | 1 | -2.720 | 2.720 | 0% | 0.118 | 0.221 | 1.194 | 0.072 |
| growth | 177 | -0.815 | 0.871 | 10% | 0.725 | 0.491 | 0.625 | 0.544 |
| mature | 804 | -0.453 | 0.546 | 19% | 0.492 | 0.327 | 0.568 | 0.374 |
| maturing | 389 | -0.510 | 0.597 | 19% | 0.655 | 0.395 | 0.549 | 0.456 |

By cyclical flag (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| False | 800 | -0.443 | 0.549 | 21% | 0.498 | 0.353 | 0.573 | 0.394 |
| True | 635 | -0.583 | 0.660 | 13% | 0.595 | 0.388 | 0.583 | 0.431 |

By multiple today (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| 10-15x | 133 | -0.198 | 0.431 | 29% | 0.637 | 0.416 | 0.625 | 0.419 |
| 15-20x | 222 | -0.333 | 0.421 | 26% | 0.537 | 0.326 | 0.528 | 0.371 |
| 20-30x | 484 | -0.437 | 0.509 | 21% | 0.523 | 0.358 | 0.528 | 0.397 |
| 30-60x | 404 | -0.689 | 0.704 | 9% | 0.552 | 0.336 | 0.545 | 0.409 |
| <10x | 51 | 0.595 | 0.701 | 12% | 0.509 | 0.347 | 0.599 | 0.459 |
| >60x (capped) | 141 | -1.410 | 1.430 | 9% | 0.672 | 0.497 | 0.839 | 0.532 |

By size class (lane_base bias / MdALE / within25; then the baselines' MdALE):

| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |
|---|---|---|---|---|---|---|---|---|
| $0-325m | 4 | -1.491 | 1.593 | 0% | 0.927 | 0.589 | 1.186 | 0.614 |
| $1,250-2,000m | 85 | -0.625 | 0.656 | 12% | 0.642 | 0.430 | 0.637 | 0.471 |
| $12,000-25,000m | 281 | -0.362 | 0.471 | 25% | 0.492 | 0.333 | 0.521 | 0.391 |
| $2,000-3,000m | 136 | -0.576 | 0.587 | 16% | 0.575 | 0.382 | 0.601 | 0.462 |
| $3,000-4,500m | 143 | -0.551 | 0.601 | 15% | 0.614 | 0.327 | 0.493 | 0.388 |
| $325-700m | 29 | -1.143 | 1.143 | 0% | 0.854 | 0.531 | 0.517 | 0.705 |
| $4,500-7,000m | 172 | -0.555 | 0.576 | 15% | 0.635 | 0.366 | 0.545 | 0.404 |
| $7,000-12,000m | 232 | -0.650 | 0.709 | 14% | 0.583 | 0.393 | 0.620 | 0.447 |
| $700-1,250m | 43 | -0.956 | 0.956 | 5% | 0.942 | 0.604 | 0.546 | 0.679 |
| >$25,000m | 141 | -0.316 | 0.543 | 20% | 0.437 | 0.337 | 0.615 | 0.350 |
| >$50,000m | 169 | -0.256 | 0.496 | 25% | 0.463 | 0.334 | 0.622 | 0.367 |

Lane-only sample (lane rows with a realized price; no engine or EPS requirement):

| predictor | n | bias | MdALE | within25 |
|---|---|---|---|---|
| lane_base | 1791 | -0.528 | 0.620 | 17% |
| lane_bear | 1791 | -1.160 | 1.186 | 6% |
| lane_bull | 1791 | -0.043 | 0.399 | 30% |
| random_walk | 1791 | -0.514 | 0.571 | 20% |
| drift_7 | 1791 | -0.175 | 0.373 | 31% |

Lane-only band: within 39%, below bear 8%, above bull 53%; head to head vs random_walk 46%, vs drift_7 29%

## Diagnostics (6.6; no effect on the price)

Lane rows ok 3287 of 4199.

| size class | n | median lambda (base) | median 5-year base-rate share (base) | share of rows shrunk |
|---|---|---|---|---|
| $0-325m | 10 | 1.000 | 0.157 | 0% |
| $1,250-2,000m | 174 | 1.000 | 0.483 | 6% |
| $12,000-25,000m | 670 | 1.000 | 0.504 | 7% |
| $2,000-3,000m | 297 | 1.000 | 0.548 | 5% |
| $3,000-4,500m | 327 | 1.000 | 0.500 | 8% |
| $325-700m | 64 | 1.000 | 0.447 | 11% |
| $4,500-7,000m | 403 | 1.000 | 0.505 | 5% |
| $7,000-12,000m | 545 | 1.000 | 0.512 | 5% |
| $700-1,250m | 89 | 1.000 | 0.441 | 13% |
| >$25,000m | 335 | 1.000 | 0.486 | 5% |
| >$50,000m | 373 | 1.000 | 0.438 | 10% |

Premium today / multiple today (D_0 / M_0): p10 -0.410, p25 0.039, median 0.319, p75 0.511, p90 0.635 (n 3281).
Multiple today median 26.2x; justified today median 16.8x.
Justified multiple today / Damodaran Jan-2026 regression PE: p25 0.439, median 0.545, p75 0.673 (Damodaran January-2026 fit: diagnostic only, not point in time).
Flag shares: cyclical_margin_history_short 0%, cyclical_normalized 49%, cyclical_trend_missing 1%, lambda_shrunk_base 7%, lambda_shrunk_bull 6%, margin_floored_base 2%, margin_floored_bear 19%, margin_floored_bull 0%, multiple_capped 11%, ni_ttm_from_facts 2%, roic_missing 11%, run_rate_clipped 1%, share_pace_clipped 9%

## Checks (6.7)

- S1_h3_below_analyst_and_engine: FAIL -- lane 0.399, analyst_const 0.374, engine_irr 0.286
- S1_h5_below_analyst_and_engine: FAIL -- lane 0.588, analyst_const 0.577, engine_irr 0.408
- S1_h1_not_worse_than_mechanical_by_0.03: FAIL -- lane 0.213, min_mechanical 0.173
- S1_h3_not_worse_than_mechanical_by_0.03: FAIL -- lane 0.399, min_mechanical 0.278
- S1_h5_not_worse_than_mechanical_by_0.03: FAIL -- lane 0.588, min_mechanical 0.363
- S2_h1_bias_within_0.10: FAIL -- bias_median -0.113
- S2_h3_bias_within_0.10: FAIL -- bias_median -0.311
- S2_h5_bias_within_0.10: FAIL -- bias_median -0.504
- S3_h3_band: FAIL -- n 2023, within_band 0.538, below_bear 0.066, above_bull 0.396
- S3_h5_band: FAIL -- n 1435, within_band 0.401, below_bear 0.077, above_bull 0.522

## Caveats

- survivorship: the universe is today's largest US companies, so every realized price is a survivor's; the deflated check (6.8) rescales each window to SPY and is reported next to the raw results, not instead of them
- in-sample design: the stage parameters, base rates and the method were written in 2026 with the period's history known; the preregistration fixed them before this run, but they are not out of sample
- no guidance or consensus in the backtest: the run-rate + base-rate variant only; guided paths are untested
- one formation a year: yearly rank statistics with t below about 2 are noise
- the engine_irr baseline comes from the engine walk-forward rows supplied (scripts/backtest_engine.py)
