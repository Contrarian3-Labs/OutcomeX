// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {MachineAssetNFT} from "../src/MachineAssetNFT.sol";
import {OrderBook} from "../src/OrderBook.sol";
import {OrderPaymentRouter} from "../src/OrderPaymentRouter.sol";
import {PWRToken} from "../src/PWRToken.sol";
import {RevenueVault} from "../src/RevenueVault.sol";
import {SettlementController} from "../src/SettlementController.sol";
import {SimpleERC20} from "../src/common/SimpleERC20.sol";
import {IPermit2} from "../src/interfaces/IPermit2.sol";
import {OrderRecord, OrderStatus} from "../src/types/OutcomeXTypes.sol";
import {TestBase} from "./utils/TestBase.sol";

contract MockUSDCWithAuthorization is SimpleERC20 {
    mapping(address => mapping(bytes32 => bool)) public authorizationUsed;

    constructor() SimpleERC20("USD Coin", "USDC", 6) {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function receiveWithAuthorization(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8,
        bytes32,
        bytes32
    ) external {
        require(block.timestamp >= validAfter, "AUTH_NOT_YET_VALID");
        require(block.timestamp <= validBefore, "AUTH_EXPIRED");
        require(!authorizationUsed[from][nonce], "AUTH_ALREADY_USED");
        authorizationUsed[from][nonce] = true;
        _transfer(from, to, value);
    }
}

contract MockUSDT is SimpleERC20 {
    constructor() SimpleERC20("Tether", "USDT", 6) {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

contract MockPermit2 is IPermit2 {
    function permitTransferFrom(
        PermitTransferFrom calldata permit,
        SignatureTransferDetails calldata transferDetails,
        address owner,
        bytes calldata signature
    ) external {
        require(signature.length > 0, "EMPTY_SIGNATURE");
        require(block.timestamp <= permit.deadline, "PERMIT_EXPIRED");
        require(transferDetails.requestedAmount <= permit.permitted.amount, "AMOUNT_EXCEEDS_PERMIT");
        bool success =
            SimpleERC20(permit.permitted.token).transferFrom(owner, transferDetails.to, transferDetails.requestedAmount);
        require(success, "TRANSFER_FAILED");
    }
}

contract OrderPaymentRouterTest is TestBase {
    address internal constant ADMIN = address(0xA11CE);
    address internal constant PLATFORM_TREASURY = address(0xBEEF);
    address internal constant MACHINE_OWNER = address(0xCAFE);
    address internal constant BUYER = address(0xB0B);

    MockUSDCWithAuthorization internal usdc;
    MockUSDT internal usdt;
    MockPermit2 internal permit2;
    PWRToken internal pwr;
    MachineAssetNFT internal machineAsset;
    RevenueVault internal revenueVault;
    SettlementController internal settlement;
    OrderBook internal orderBook;
    OrderPaymentRouter internal router;

    uint256 internal machineId;

    function setUp() public {
        usdc = new MockUSDCWithAuthorization();
        usdt = new MockUSDT();
        permit2 = new MockPermit2();
        pwr = new PWRToken(ADMIN);
        machineAsset = new MachineAssetNFT(ADMIN);
        revenueVault = new RevenueVault(ADMIN, address(pwr), address(machineAsset));
        settlement = new SettlementController(ADMIN, address(revenueVault), PLATFORM_TREASURY);
        orderBook = new OrderBook(ADMIN, address(machineAsset));
        router = new OrderPaymentRouter(ADMIN, address(orderBook), address(usdc), address(usdt), address(pwr), address(permit2));

        vm.startPrank(ADMIN);
        pwr.setMinter(address(revenueVault), true);
        usdc.mint(BUYER, 5_000_000);
        usdt.mint(BUYER, 5_000_000);
        revenueVault.setSettlementController(address(settlement));
        router.setSettlementEscrow(address(settlement));
        settlement.setOrderBook(address(orderBook));
        orderBook.setSettlementController(address(settlement));
        orderBook.setRevenueVault(address(revenueVault));
        orderBook.setPaymentAdapter(address(router));
        machineAsset.setTransferGuard(address(orderBook));
        machineId = machineAsset.mintMachine(MACHINE_OWNER, "ipfs://machine-001");
        vm.stopPrank();
    }

    function testUSDCFailedBeforePreviewCanRefundRealFunds() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000_000);

        vm.prank(BUYER);
        router.payWithUSDCByAuthorization(
            orderId, 1_000_000, block.timestamp - 1, block.timestamp + 1 days, keccak256("nonce-1"), 0, bytes32(0), bytes32(0)
        );

        assertEq(usdc.balanceOf(address(settlement)), 1_000_000, "settlement should escrow usdc");

        vm.prank(BUYER);
        orderBook.refundFailedOrNoValidPreview(orderId);

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(uint256(order.status), uint256(OrderStatus.Refunded), "expected refunded status");
        assertEq(settlement.refundableByToken(BUYER, address(usdc)), 1_000_000, "refund ledger mismatch");

        vm.prank(BUYER);
        uint256 claimed = settlement.claimRefund(address(usdc));
        assertEq(claimed, 1_000_000, "refund amount mismatch");
        assertEq(usdc.balanceOf(BUYER), 5_000_000, "buyer should recover all usdc");
        assertEq(usdc.balanceOf(address(settlement)), 0, "settlement escrow should be empty");
    }

    function testCreateAndPayWithUSDCRecordsBuyerAsCaller() public {
        vm.prank(BUYER);
        uint256 orderId = router.createOrderAndPayWithUSDC(
            machineId, 1_000_000, block.timestamp - 1, block.timestamp + 1 days, keccak256("nonce-create-pay-1"), 0, bytes32(0), bytes32(0)
        );

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(order.buyer, BUYER, "buyer should be external caller");
        assertEq(uint256(order.status), uint256(OrderStatus.Paid), "order should be paid");
        assertEq(usdc.balanceOf(address(settlement)), 1_000_000, "settlement should escrow usdc");
    }

    function testCreateAndPayWithUSDTRecordsBuyerAsCaller() public {
        vm.prank(BUYER);
        usdt.approve(address(permit2), 1_000_000);

        vm.prank(BUYER);
        uint256 orderId = router.createOrderAndPayWithUSDT(machineId, 1_000_000, 1, block.timestamp + 1 days, hex"BEEF");

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(order.buyer, BUYER, "buyer should be external caller");
        assertEq(uint256(order.status), uint256(OrderStatus.Paid), "order should be paid");
        assertEq(usdt.balanceOf(address(settlement)), 1_000_000, "settlement should escrow usdt");
    }

    function testCreateAndPayWithPWRRecordsBuyerAsCaller() public {
        vm.startPrank(ADMIN);
        pwr.setMinter(ADMIN, true);
        pwr.mint(BUYER, 1_000);
        vm.stopPrank();

        vm.prank(BUYER);
        pwr.approve(address(router), 1_000);

        vm.prank(BUYER);
        uint256 orderId = router.createOrderAndPayWithPWR(machineId, 1_000);

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(order.buyer, BUYER, "buyer should be external caller");
        assertEq(uint256(order.status), uint256(OrderStatus.Paid), "order should be paid");
        assertEq(pwr.balanceOf(address(settlement)), 1_000, "settlement should escrow pwr");
    }

    function testCreatePaidOrderByAdapterBlocksTransferImmediately() public {
        vm.prank(ADMIN);
        uint256 orderId = router.createPaidOrderByAdapter(BUYER, machineId, 1_000_000, address(usdc));

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(order.buyer, BUYER, "buyer should match adapter input");
        assertEq(uint256(order.status), uint256(OrderStatus.Paid), "order should be paid");
        assertEq(orderBook.activeTaskCountByMachine(machineId), 1, "active task should be tracked");

        (bool canTransfer, bytes32 reason) = orderBook.canTransfer(machineId, MACHINE_OWNER, address(0xDEAD));
        assertTrue(!canTransfer, "transfer should be blocked");
        assertEq(reason, orderBook.REASON_ACTIVE_TASK(), "active task reason mismatch");

        vm.startPrank(MACHINE_OWNER);
        vm.expectRevert(
            abi.encodeWithSelector(
                bytes4(keccak256("TransferGuardBlocked(uint256,bytes32)")), machineId, orderBook.REASON_ACTIVE_TASK()
            )
        );
        machineAsset.transferFrom(MACHINE_OWNER, address(0xDEAD), machineId);
        vm.stopPrank();
    }

    function testLegacyCreateOrderThenPayWithPWRStillWorks() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000);

        vm.startPrank(ADMIN);
        pwr.setMinter(ADMIN, true);
        pwr.mint(BUYER, 1_000);
        vm.stopPrank();

        vm.prank(BUYER);
        pwr.approve(address(router), 1_000);

        vm.prank(BUYER);
        router.payWithPWR(orderId, 1_000);

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(order.buyer, BUYER, "legacy path should keep original buyer");
        assertEq(uint256(order.status), uint256(OrderStatus.Paid), "legacy path should mark paid");
    }

    function testUSDTConfirmedOrderCreatesRealPlatformClaimAndReserve() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000_000);

        vm.prank(BUYER);
        usdt.approve(address(permit2), 1_000_000);

        vm.prank(BUYER);
        router.payWithUSDT(orderId, 1_000_000, 1, block.timestamp + 1 days, hex"BEEF");

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, true);

        vm.prank(BUYER);
        orderBook.confirmResult(orderId);

        assertEq(settlement.platformAccruedByToken(address(usdt)), 100_000, "platform usdt claim mismatch");
        assertEq(revenueVault.unsettledRevenueByMachine(machineId), 900_000, "machine pwr claim mismatch");
        assertEq(usdt.balanceOf(address(settlement)), 1_000_000, "escrow should hold full reserve before claims");

        vm.prank(PLATFORM_TREASURY);
        uint256 claimed = settlement.claimPlatformRevenue(address(usdt));
        assertEq(claimed, 100_000, "platform claim amount mismatch");
        assertEq(usdt.balanceOf(PLATFORM_TREASURY), 100_000, "platform should receive real usdt");
        assertEq(usdt.balanceOf(address(settlement)), 900_000, "remaining usdt should stay as reserve backing");
    }

    function testPWRConfirmedOrderCreatesPlatformClaimAndMachineReserve() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000);

        vm.startPrank(ADMIN);
        pwr.setMinter(ADMIN, true);
        pwr.mint(BUYER, 1_000);
        vm.stopPrank();

        vm.prank(BUYER);
        pwr.approve(address(router), 1_000);

        vm.prank(BUYER);
        router.payWithPWR(orderId, 1_000);

        assertEq(pwr.balanceOf(address(settlement)), 1_000, "settlement should escrow pwr");

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, true);

        vm.prank(BUYER);
        orderBook.confirmResult(orderId);

        assertEq(settlement.platformAccruedByToken(address(pwr)), 100, "platform pwr claim mismatch");
        assertEq(revenueVault.unsettledRevenueByMachine(machineId), 900, "machine pwr accrual mismatch");
    }

    function testPWRFailedBeforePreviewCanRefundPaidPWR() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000);

        vm.startPrank(ADMIN);
        pwr.setMinter(ADMIN, true);
        pwr.mint(BUYER, 1_000);
        vm.stopPrank();

        vm.prank(BUYER);
        pwr.approve(address(router), 1_000);

        vm.prank(BUYER);
        router.payWithPWR(orderId, 1_000);

        vm.prank(BUYER);
        orderBook.refundFailedOrNoValidPreview(orderId);

        assertEq(settlement.refundableByToken(BUYER, address(pwr)), 1_000, "refund ledger mismatch");

        vm.prank(BUYER);
        uint256 claimed = settlement.claimRefund(address(pwr));
        assertEq(claimed, 1_000, "refund amount mismatch");
        assertEq(pwr.balanceOf(BUYER), 1_000, "buyer should recover all pwr");
        assertEq(pwr.balanceOf(address(settlement)), 0, "settlement escrow should be empty");
    }
}
