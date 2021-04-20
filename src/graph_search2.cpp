#include "motion_primitives/graph_search2.h"

#include <glog/logging.h>
#include <ros/init.h>  // ok()
#include <tbb/enumerable_thread_specific.h>
#include <tbb/parallel_for.h>

#include <boost/timer/timer.hpp>

namespace motion_primitives {

namespace {

double Elapsed(const boost::timer::cpu_timer& timer) noexcept {
  return timer.elapsed().wall / 1e9;
}

bool state_pos_within(const Eigen::VectorXd& p1, const Eigen::VectorXd& p2,
                      int spatial_dim, double d) noexcept {
  return (p1.head(spatial_dim) - p2.head(spatial_dim)).squaredNorm() < (d * d);
}

}  // namespace

std::size_t VectorXdHash::operator()(const Eigen::VectorXd& vd) const noexcept {
  using std::size_t;

  // allow sufficiently close state to map to the same hash value
  const Eigen::VectorXi v = (vd * 100).cast<int>();

  size_t seed = 0;
  for (size_t i = 0; i < static_cast<size_t>(v.size()); ++i) {
    const auto elem = *(v.data() + i);
    seed ^= std::hash<int>()(elem) + 0x9e3779b9 + (seed << 6) + (seed >> 2);
  }
  return seed;
}

auto GraphSearch2::Expand(const Node2& node) const -> std::vector<Node2> {
  std::vector<Node2> nodes;
  nodes.reserve(64);

  const int state_index = graph_.NormIndex(node.state_index);
  const auto num_states = static_cast<int>(graph_.edges_.rows());

  for (int i = 0; i < num_states; ++i) {
    if (graph_.edges_(i, state_index) < 0) continue;

    auto mp = graph_.get_mp_between_indices(i, state_index);
    mp.translate(node.state);

    // Check if already visited
    if (visited_states_.find(mp.end_state()) != visited_states_.end()) continue;

    // Then check if its collision free
    if (!is_mp_collision_free(mp)) continue;

    // This is a good next node
    Node2 next_node;
    next_node.state_index = i;
    next_node.state = mp.end_state();
    next_node.motion_cost = node.motion_cost + mp.cost_;
    next_node.heuristic_cost = heuristic(mp.end_state());
    nodes.push_back(next_node);
  }

  return nodes;
}

auto GraphSearch2::ExpandPar(const Node2& node) const -> std::vector<Node2> {
  const int state_index = graph_.NormIndex(node.state_index);
  const auto num_states = static_cast<int>(graph_.edges_.rows());

  using PrivVec = tbb::enumerable_thread_specific<std::vector<Node2>>;
  PrivVec priv_nodes;

  tbb::parallel_for(
      tbb::blocked_range<int>(0, num_states),
      [&, this](const tbb::blocked_range<int>& r) {
        auto& local = priv_nodes.local();

        for (int i = r.begin(); i < r.end(); ++i) {
          if (graph_.edges_(i, state_index) < 0) continue;

          auto mp = graph_.get_mp_between_indices(i, state_index);
          mp.translate(node.state);

          // Check if already visited
          if (visited_states_.find(mp.end_state()) != visited_states_.end()) {
            continue;
          }

          // Then check if its collision free
          if (!is_mp_collision_free(mp)) continue;

          // This is a good next node
          Node2 next_node;
          next_node.state_index = i;
          next_node.state = mp.end_state();
          next_node.motion_cost = node.motion_cost + mp.cost_;
          next_node.heuristic_cost = heuristic(mp.end_state());

          local.push_back(std::move(next_node));
        }
      });

  // combine
  std::vector<Node2> nodes;
  nodes.reserve(64);
  //  for (auto i = priv_nodes.begin(); i != priv_nodes.end(); ++i) {
  for (const auto& each : priv_nodes) {
    //    const auto& each = *i;
    nodes.insert(nodes.end(), each.begin(), each.end());
  }
  return nodes;
}

MotionPrimitive GraphSearch2::GetPrimitiveBetween(const Node2& start_node,
                                                  const Node2& end_node) const {
  const int start_index = graph_.NormIndex(start_node.state_index);
  auto mp = graph_.get_mp_between_indices(end_node.state_index, start_index);
  mp.translate(start_node.state);
  return mp;
}

std::vector<MotionPrimitive> GraphSearch2::RecoverPath(
    const PathHistory& history, const Node2& end_node) const {
  std::vector<MotionPrimitive> path_mps;
  Node2 const* curr_node = &end_node;

  while (ros::ok()) {
    if (curr_node->motion_cost == 0) break;
    Node2 const* prev_node = &(history.at(curr_node->state).parent_node);
    path_mps.push_back(GetPrimitiveBetween(*prev_node, *curr_node));
    curr_node = prev_node;
  }

  std::reverse(path_mps.begin(), path_mps.end());
  return path_mps;
}

auto GraphSearch2::Search(const Eigen::VectorXd& start_state,
                          const Eigen::VectorXd& end_state,
                          double distance_threshold, bool parallel) const
    -> std::vector<MotionPrimitive> {
  // Debug
  LOG(INFO) << "adj mat: " << graph_.edges_.rows() << " "
            << graph_.edges_.cols() << ", nnz: " << (graph_.edges_ > 0).count();
  LOG(INFO) << "mps: " << graph_.mps_.size();
  LOG(INFO) << "verts: " << graph_.vertices_.rows() << " "
            << graph_.vertices_.cols();

  timings.clear();
  visited_states_.clear();

  // Early exit if start and end positions are close
  if (state_pos_within(start_state, end_state, spatial_dim(),
                       distance_threshold)) {
    return {};
  }

  Node2 start_node;
  start_node.state_index = 0;
  start_node.state = start_state;
  start_node.motion_cost = 0.0;
  start_node.heuristic_cost = heuristic(start_state);

  // > for min heap
  auto node_cmp = [](const Node2& n1, const Node2& n2) {
    return n1.total_cost() > n2.total_cost();
  };
  using MinHeap =
      std::priority_queue<Node2, std::vector<Node2>, decltype(node_cmp)>;

  MinHeap pq{node_cmp};
  pq.push(start_node);

  // Shortest path history, stores the parent node of a particular mp (int)
  PathHistory history;

  // timer
  boost::timer::cpu_timer timer;

  while (!pq.empty() && ros::ok()) {
    Node2 curr_node = pq.top();

    // Check if we are close enough to the end
    if (state_pos_within(curr_node.state, end_state, spatial_dim(),
                         distance_threshold)) {
      LOG(INFO) << "== pq: " << pq.size();
      LOG(INFO) << "== hist: " << history.size();
      LOG(INFO) << "== nodes: " << visited_states_.size();
      return RecoverPath(history, curr_node);
    }

    timer.start();
    pq.pop();
    timings["astar_pop"] += Elapsed(timer);

    // Due to the imutability of std::priority_queue, we have no way of
    // modifying the priority of an element in the queue. Therefore, when we
    // push the next node into the queue, there might be duplicated nodes with
    // the same state but different costs. This could cause us to expand the
    // same state multiple times.
    // Although this does not affect the correctness of the implementation
    // (since the nodes are correctly sorted), it might be slower to repeatedly
    // expanding visited states. The timiing suggest more than 80% of the time
    // is spent on the Expand(node) call. Thus, we will check here if this state
    // has been visited and skip if it has. This will save around 20%
    // computation.
    if (visited_states_.find(curr_node.state) != visited_states_.cend()) {
      continue;
    }
    // add current state to visited
    visited_states_.insert(curr_node.state);

    timer.start();
    const auto next_nodes = parallel ? ExpandPar(curr_node) : Expand(curr_node);
    timings["astar_expand"] += Elapsed(timer);

    for (const auto& next_node : next_nodes) {
      // this is the best cost reaching this state (next_node) so far
      // could be inf if this state has never been visited
      const auto best_cost = history[next_node.state].best_cost;

      // compare reaching next_node from curr_node and mp to best cost
      if (next_node.motion_cost < best_cost) {
        timer.start();
        pq.push(next_node);
        timings["astar_push"] += Elapsed(timer);
        history[next_node.state] = {curr_node, next_node.motion_cost};
      }
    }
  }

  return {};
}

std::vector<Eigen::VectorXd> GraphSearch2::GetVisitedStates() const noexcept {
  return {visited_states_.cbegin(), visited_states_.cend()};
}

}  // namespace motion_primitives
