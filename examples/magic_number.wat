(module
  (import "env" "now_ms" (func $now_ms (result i64)))
  (memory (export "memory") 1)

  ;; compute(seed_offset: i32, input_len: i32, out_offset: i32, out_size: i32) -> i32
  ;; Searches for a number N where N % target == 0
  ;; Input: 8 bytes = target (u64), 8 bytes = seed_start (u64)
  ;; Output: if found, writes (seed: u64) at out_offset, returns 1
  ;;          else writes 0, returns 0
  (func (export "compute")
        (param $seed_offset i32) (param $input_len i32)
        (param $out_offset i32) (param $out_size i32)
        (result i32)

    (local $target i64)
    (local $seed i64)
    (local $count i32)
    (local $remainder i64)

    ;; Read target (8 bytes) and seed (8 bytes) from input
    (local.set $target (i64.load (local.get $seed_offset)))
    (local.set $seed (i64.load (i32.add (local.get $seed_offset) (i32.const 8))))
    (local.set $count (i32.const 0))

    (if (i64.eqz (local.get $target))
      (then (local.set $target (i64.const 1))) ;; avoid div by zero
    )

    (loop $search
      (local.set $remainder (i64.rem_u (local.get $seed) (local.get $target)))

      (if (i64.eqz (local.get $remainder))
        (then
          ;; Found! Write seed to output
          (i64.store (local.get $out_offset) (local.get $seed))
          (return (i32.const 1)) ;; success
        )
      )

      ;; Next candidate
      (local.set $seed (i64.add (local.get $seed) (i64.const 1)))
      (local.set $count (i32.add (local.get $count) (i32.const 1)))

      ;; Stop after 50k attempts per task
      (br_if $search (i32.lt_u (local.get $count) (i32.const 50000)))
    )

    ;; Not found
    (i64.store (local.get $out_offset) (i64.const 0))
    (i32.const 0)
  )
)
