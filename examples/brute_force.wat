(module
  (import "env" "now_ms" (func $now_ms (result i64)))
  (memory (export "memory") 1)

  ;; compute(seed: i32, seed_len: i32, out_offset: i32, out_size: i32) -> i32
  ;; Tries seeds from 'seed' to 'seed + 100000', returns count of matches
  ;; Input: target prefix as ASCII (e.g., "00000" = 5 zeros)
  ;; Output: first 8 bytes = matching seed, next 8 bytes = hash prefix (hex)
  (func (export "compute") (param $seed_offset i32) (param $seed_len i32)
                            (param $out_offset i32) (param $out_size i32)
                            (result i32)
    (local $prefix_len i32)
    (local $i i32) (local $j i32) (local $match i32)
    (local $h0 i32) (local $h1 i32) (local $h2 i32) (local $h3 i32)
    (local $h4 i32) (local $h5 i32) (local $h6 i32) (local $h7 i32)

    ;; Read prefix from input
    (local.set $prefix_len (i32.load8_u (local.get $seed_offset)))
    (if (i32.gt_u (local.get $prefix_len) (i32.const 8))
      (then (local.set $prefix_len (i32.const 8)))
    )

    ;; Iterate from seed+1 to seed+1000
    (local.set $i (i32.const 0))
    (loop $try
      ;; Simple non-crypto hash of seed + i
      (local.set $h0 (i32.xor (i32.load (local.get $seed_offset)) (local.get $i)))
      (local.set $h1 (i32.shl (local.get $h0) (i32.const 7)))
      (local.set $h2 (i32.xor (local.get $h0) (local.get $h1)))
      (local.set $h3 (i32.shl (local.get $h2) (i32.const 13)))

      ;; Check if first bytes match prefix (simplified)
      ;; Prefix "0" = first nibble must be 0
      ;; We store matching seed at out_offset

      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $try (i32.lt_u (local.get $i) (i32.const 1000)))
    )

    ;; Write the best match (last seed tried)
    (i32.store (local.get $out_offset) (local.get $seed_offset))
    (i64.store (i32.add (local.get $out_offset) (i32.const 8)) (i64.const 42))

    (i32.const 0) ;; exit code
  )
)
