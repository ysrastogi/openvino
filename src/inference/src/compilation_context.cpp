// Copyright (C) 2018-2024 Intel Corporation
// SPDX-License-Identifier: Apache-2.0
//

#include "compilation_context.hpp"

#include <sys/stat.h>
#include <sys/types.h>

#ifndef _WIN32
#    include <unistd.h>
#endif

#include "itt.hpp"
#include "openvino/pass/manager.hpp"
#include "openvino/util/file_util.hpp"
#include "openvino/util/xml_parse_utils.hpp"
#include "transformations/hash.hpp"
#include "transformations/rt_info/fused_names_attribute.hpp"
#include "transformations/rt_info/primitives_priority_attribute.hpp"

#ifdef _WIN32
#    define stat _stat
#endif

namespace ov {

template <typename T>
static uint64_t hash_combine(uint64_t seed, const T& a) {
    // Hash combine formula from boost
    return seed ^ (std::hash<T>()(a) + 0x9e3779b9 + (seed << 6) + (seed >> 2));
}

template <typename T>
static int32_t as_int32_t(T v) {
    return static_cast<int32_t>(v);
}

}  // namespace ov

namespace ov {

std::string ModelCache::calculate_file_info(const std::string& filePath) {
    uint64_t seed = 0;
    auto absPath = filePath;
    if (filePath.size() > 0) {
        try {
            absPath = ov::util::get_absolute_file_path(filePath);
        } catch (std::runtime_error&) {
            // can't get absolute path, will use filePath for hash
        }
    }

    seed = hash_combine(seed, absPath);

    std::string res;
    struct stat result;
    if (stat(absPath.c_str(), &result) == 0) {
        seed = hash_combine(seed, result.st_mtime);
        seed = hash_combine(seed, result.st_size);
    }
    return std::to_string(seed);
}

std::string ModelCache::compute_hash(const std::shared_ptr<const ov::Model>& model, const ov::AnyMap& compileOptions) {
    OV_ITT_SCOPE(FIRST_INFERENCE, ov::itt::domains::ReadTime, "ModelCache::compute_hash - Model");

    OPENVINO_ASSERT(model);

    uint64_t seed = 0;
    // 1. Calculate hash on function
    ov::pass::Manager m;
    m.register_pass<ov::pass::Hash>(seed);
    m.run_passes(std::const_pointer_cast<ov::Model>(model));

    // 2. Compute hash on serialized data and options
    for (const auto& kvp : compileOptions) {
        seed = ov::hash_combine(seed, kvp.first + kvp.second.as<std::string>());
    }

    // 3. Add runtime information which may not be serialized
    for (const auto& op : model->get_ordered_ops()) {
        const auto& rt = op->get_rt_info();
        for (const auto& rtMapData : rt) {
            seed = ov::hash_combine(seed, rtMapData.first);
            std::stringstream strm;
            rtMapData.second.print(strm);
            seed = ov::hash_combine(seed, strm.str());
        }
    }

    return std::to_string(seed);
}

std::string ModelCache::compute_hash(const std::string& modelName, const ov::AnyMap& compileOptions) {
    OV_ITT_SCOPE(FIRST_INFERENCE, ov::itt::domains::ReadTime, "ModelCache::compute_hash - ModelName");
    uint64_t seed = 0;
    try {
        seed = hash_combine(seed, ov::util::get_absolute_file_path(modelName));
    } catch (...) {
        // can't get absolute path, use modelName for hash calculation
        seed = hash_combine(seed, modelName);
    }
    for (const auto& kvp : compileOptions) {
        seed = hash_combine(seed, kvp.first + kvp.second.as<std::string>());
    }
    return std::to_string(seed);
}

std::string ModelCache::compute_hash(const std::string& modelStr,
                                     const ov::Tensor& tensor,
                                     const ov::AnyMap& compileOptions) {
    OV_ITT_SCOPE(FIRST_INFERENCE, ov::itt::domains::ReadTime, "ModelCache::compute_hash - Model Memory");
    uint64_t seed = 0;
    // model string
    seed = hash_combine(seed, modelStr);

    // tensor data
    if (tensor) {
        seed = hash_combine(seed, tensor.get_size());

        auto ptr = static_cast<size_t*>(tensor.data());
        size_t size = tensor.get_size() / sizeof(size_t);
        for (size_t i = 0; i < size; i++)
            seed = hash_combine(seed, ptr[i]);
        auto size_done = size * sizeof(size_t);
        auto ptr_left = static_cast<uint8_t*>(tensor.data()) + size_done;
        size_t size_left = tensor.get_size() - size_done;
        for (size_t i = 0; i < size_left; i++)
            seed = hash_combine(seed, ptr_left[i]);
    }

    // compile options
    for (const auto& kvp : compileOptions) {
        seed = hash_combine(seed, kvp.first + kvp.second.as<std::string>());
    }
    return std::to_string(seed);
}

//////////////////////////////////////////////////

CompiledBlobHeader::CompiledBlobHeader() {}

CompiledBlobHeader::CompiledBlobHeader(const std::string& ieVersion,
                                       const std::string& fileInfo,
                                       const std::string& runtimeInfo)
    : m_ieVersion(ieVersion),
      m_fileInfo(fileInfo),
      m_runtimeInfo(runtimeInfo) {}

std::istream& operator>>(std::istream& stream, CompiledBlobHeader& header) {
    std::string xmlStr;
    std::getline(stream, xmlStr);

    pugi::xml_document document;
    pugi::xml_parse_result res = document.load_string(xmlStr.c_str());
    OPENVINO_ASSERT(res.status == pugi::status_ok, "Error reading compiled blob header");

    pugi::xml_node compiledBlobNode = document.document_element();
    header.m_ieVersion = ov::util::pugixml::get_str_attr(compiledBlobNode, "ie_version");
    header.m_fileInfo = ov::util::pugixml::get_str_attr(compiledBlobNode, "file_info");
    header.m_runtimeInfo = ov::util::pugixml::get_str_attr(compiledBlobNode, "runtime_info");

    return stream;
}

std::ostream& operator<<(std::ostream& stream, const CompiledBlobHeader& header) {
    pugi::xml_document document;
    auto compiledBlobNode = document.append_child("compiled_blob");
    compiledBlobNode.append_attribute("ie_version").set_value(header.m_ieVersion.c_str());
    compiledBlobNode.append_attribute("file_info").set_value(header.m_fileInfo.c_str());
    compiledBlobNode.append_attribute("runtime_info").set_value(header.m_runtimeInfo.c_str());

    document.save(stream, nullptr, pugi::format_raw);
    document.reset();
    stream << std::endl;

    return stream;
}

}  // namespace ov
