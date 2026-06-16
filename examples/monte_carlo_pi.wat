;; monte_carlo_pi.wat — estimate π via Monte Carlo
;; Input:  8 bytes (i64 seed for PRNG)
;; Output: 16 bytes (i64 points_inside, i64 points_total)
(module
  (memory (export "memory") 1)

  ;; XorShift64* PRNG
  (func $xorshift (param $state_ptr i32) (result i64)
    (local $x i64)
    (local.set $x (i64.load (local.get $state_ptr)))
    (local.set $x (i64.xor (local.get $x) (i64.shl (local.get $x) (i64.const 12))))
    (local.set $x (i64.xor (local.get $x) (i64.shr_u (local.get $x) (i64.const 25))))
    (local.set $x (i64.xor (local.get $x) (i64.shl (local.get $x) (i64.const 27))))
    (i64.store (local.get $state_ptr) (local.get $x))
    (i64.mul (local.get $x) (i64.const 2685821657736338717))
  )

  (func (export "compute") (param $in_ptr i32) (param $in_len i32)
                            (param $out_ptr i32) (param $out_len i32)
                            (result i32)
    (local $seed i64)
    (local $inside i64)
    (local $total i64)
    (local $rx i64)
    (local $ry i64)
    (local $x f64)
    (local $y f64)
    (local $state_ptr i32)
    (local $max_i32 f64)

    ;; Read seed from input
    (local.set $seed (i64.load (local.get $in_ptr)))

    ;; PRNG state at offset 128
    (local.set $state_ptr (i32.const 128))
    (i64.store (local.get $state_ptr) (local.get $seed))

    (local.set $max_i32 (f64.const 4294967296.0))  ;; 2^32
    (local.set $inside (i64.const 0))
    (local.set $total (i64.const 0))

    (loop $loop
      ;; Get random x, y as u32 (high 32 bits of u64) → f64 in [0,1)
      (local.set $rx (call $xorshift (local.get $state_ptr)))
      (local.set $ry (call $xorshift (local.get $state_ptr)))

      (local.set $x (f64.div (f64.convert_i32_u (i32.wrap_i64 (i64.shr_u (local.get $rx) (i64.const 32)))) (local.get $max_i32)))
      (local.set $y (f64.div (f64.convert_i32_u (i32.wrap_i64 (i64.shr_u (local.get $ry) (i64.const 32)))) (local.get $max_i32)))

      ;; Check: x^2 + y^2 < 1.0 → inside circle
      (if (f64.lt (f64.add (f64.mul (local.get $x) (local.get $x))
                           (f64.mul (local.get $y) (local.get $y)))
                  (f64.const 1.0))
        (then
          (local.set $inside (i64.add (local.get $inside) (i64.const 1)))
        )
      )

      (local.set $total (i64.add (local.get $total) (i64.const 1)))
      (br_if $loop (i64.lt_u (local.get $total) (i64.const 100000)))
    )

    ;; Write results
    (i64.store (local.get $out_ptr) (local.get $inside))
    (i64.store (i32.add (local.get $out_ptr) (i32.const 8)) (local.get $total))
    (i32.const 0)
  )
)
