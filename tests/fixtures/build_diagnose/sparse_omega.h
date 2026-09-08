#pragma once

namespace omega_core {

class SparseOmega {
public:
    void add_node(int idx, float weight);
    float lookup(int idx) const;
    int node_count() const;
};

}  // namespace omega_core
