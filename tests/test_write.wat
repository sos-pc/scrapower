;; test_write.wat — writes known values to output
;; Verifies that the WASM runtime correctly reads output memory
(module
  (memory (export "memory") 1)
  (func (export "compute") (param $in_ptr i32) (param $in_len i32)
                            (param $out_ptr i32) (param $out_len i32)
                            (result i32)
    ;; Write known values: 42 at out_ptr, 100000 at out_ptr+8
    (i64.store (local.get $out_ptr) (i64.const 42))
    (i64.store (i32.add (local.get $out_ptr) (i32.const 8)) (i64.const 100000))
    (i32.const 0)
  )
)
