;; test_add.wat — simple WASM module: adds two numbers
;; Input:  two i32 values at memory offset 0 and 4
;; Output: sum at memory offset 0, return value = 0 (success)
(module
  (memory (export "memory") 1)
  (func (export "compute") (param $in_ptr i32) (param $in_len i32)
                            (param $out_ptr i32) (param $out_len i32)
                            (result i32)
    ;; Read two numbers from input
    (i32.store (local.get $out_ptr)
      (i32.add
        (i32.load (local.get $in_ptr))
        (i32.load (i32.add (local.get $in_ptr) (i32.const 4)))))
    (i32.const 0))  ;; exit code 0
)
