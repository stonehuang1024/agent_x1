import multiprocessing as mp

import queue
from typing import List
import time


def consumer_function_wrap(consumer_function, p_queue: mp.Queue, c_queue: mp.Queue):
    while True:
        try:
            task = p_queue.get(timeout=5)  # 设置超时以避免无限等待
            if task is None:
                # 接收到结束信号。退出循环
                break
        except queue.Empty:
            # 队列为空，等待一段时间
            time.sleep(0.01)
        else:
            consumer_function_return = consumer_function(task)
            c_queue.put(consumer_function_return)

    # 退出处理循环
    c_queue.put(None)


class ParallelFramework:
    def __new__(cls, *args, **kwargs):
        raise TypeError("ParallelFramework 不能被实例化。。")

    @staticmethod
    def multi_process_run(func, task_tokens: List, init_func=None, worker_cpu_quote: int = -1) -> List:
        manager = mp.Manager()
        producer_queue = manager.Queue(maxsize=20000)
        consumer_queue = manager.Queue(maxsize=20000)

        if worker_cpu_quote <= 0:
            worker_cpu_quote = mp.cpu_count() - 2

        worker_task_pool = mp.Pool(processes=worker_cpu_quote, initializer=init_func)

        for i in range(worker_cpu_quote):
            worker_task_pool.apply_async(consumer_function_wrap, args=(func, producer_queue, consumer_queue))

        task_num = 0
        for token in task_tokens:
            producer_queue.put(token)
            task_num += 1

        wait_flag = True
        while task_num > 0:
            if wait_flag:
                if consumer_queue.empty():
                    time.sleep(0.5)
                    continue
                else:
                    wait_flag = False

            try:
                task_result = consumer_queue.get(timeout=5)  # 设置超时以避免无限等待
            except queue.Empty:
                # 队列为空，等待一段时间
                time.sleep(0.5)
            else:
                # consumer_function_returns.append(task_result)
                # 返回 generator
                yield task_result
                task_num -= 1

        exit_task_num = 0
        for i in range(worker_cpu_quote):
            producer_queue.put(None)
            exit_task_num += 1

        while exit_task_num > 0:
            try:
                consumer_queue.get(timeout=5)  # 设置超时以避免无限等待
            except queue.Empty:
                # 队列为空，等待一段时间
                time.sleep(0.5)
            else:
                exit_task_num -= 1

        worker_task_pool.close()
        # worker_task_pool.terminate()
        worker_task_pool.join()

        # return consumer_function_returns


if __name__ == "__main__":
    tokens = ['Task1', 'Task2', 'Task3', 'Task4', '']
    # returns = ParallelFramework.multi_process_run(print, tokens)

    for val in ParallelFramework.multi_process_run(print, tokens):
        print(val)

