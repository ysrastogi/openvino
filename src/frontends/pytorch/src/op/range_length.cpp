// Copyright (C) 2018-2024 Intel Corporation
// SPDX-License-Identifier: Apache-2.0
//

#include "openvino/frontend/pytorch/node_context.hpp"
#include "openvino/op/add.hpp"
#include "openvino/op/ceiling.hpp"
#include "openvino/op/convert.hpp"
#include "openvino/op/divide.hpp"
#include "openvino/op/relu.hpp"
#include "openvino/op/subtract.hpp"
#include "utils.hpp"

namespace ov {
namespace frontend {
namespace pytorch {
namespace op {

using namespace ov::op;

OutputVector translate_range_length(const NodeContext& context) {
    // aten::__range_length(int lo, int hi, int step) -> int
    num_inputs_check(context, 3, 3);
    auto lo = context.get_input(0);
    auto hi = context.get_input(1);
    auto step = context.mark_node(std::make_shared<v0::Convert>(context.get_input(2), ov::element::f32));
    auto length = context.mark_node(std::make_shared<v1::Subtract>(hi, lo));
    auto length_f32 = context.mark_node(std::make_shared<v0::Convert>(length, ov::element::f32));
    auto num_steps = context.mark_node(std::make_shared<v1::Divide>(length_f32, step, false, AutoBroadcastType::NUMPY));
    auto ceil = context.mark_node(std::make_shared<v0::Ceiling>(num_steps));
    auto ceil_int = context.mark_node(std::make_shared<v0::Convert>(ceil, ov::element::i32));
    return {context.mark_node(std::make_shared<v0::Relu>(ceil_int))};
};

}  // namespace op
}  // namespace pytorch
}  // namespace frontend
}  // namespace ov
