;; multiply.wat — multiplie deux nombres
;; Input:  deux i32 (8 octets)
;; Output: un i32 (4 octets) = produit
(module
  (memory (export "memory") 1)
  (func (export "compute") (param $in_ptr i32) (param $in_len i32)
                            (param $out_ptr i32) (param $out_len i32)
                            (result i32)
    (i32.store (local.get $out_ptr)
      (i32.mul
        (i32.load (local.get $in_ptr))
        (i32.load (i32.add (local.get $in_ptr) (i32.const 4)))))
    (i32.const 0))
)
